#!/usr/bin/env bash
# Build every release artifact from ONE clean commit (so all of them carry the same git revision):
#   macOS arm64 + x86_64 (signed, notarized DMGs), Linux x86_64 + aarch64 (smoke-tested on clean containers).
#
#   SIGN_IDENTITY="Developer ID Application: …" NOTARY_PROFILE=valhallisc-notary scripts/release.sh
#
# Needs: .venv (arm64) and .venv-x86_64 (x86_64 Python under Rosetta, see doc/RELEASING.md), Docker with
# linux/amd64 emulation, the test IRIS container for the Linux smoke tests.
# Output: dist/release/<version>/ with the four artifacts and SHA256SUMS. Windows: GitHub Actions workflow.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${SIGN_IDENTITY:?set SIGN_IDENTITY}"; : "${NOTARY_PROFILE:?set NOTARY_PROFILE}"
if [ -n "$(git status --porcelain)" ]; then echo "commit your changes first (release builds must be clean)" >&2; exit 1; fi
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
OUT="dist/release/${VERSION}"
rm -rf "$OUT"; mkdir -p "$OUT"

echo "### macOS arm64";  PYTHON=.venv/bin/python scripts/sign-macos.sh
echo "### macOS x86_64"; arch -x86_64 env PYTHON=.venv-x86_64/bin/python bash scripts/sign-macos.sh
echo "### Linux";        scripts/build-linux.sh arm64 && scripts/build-linux.sh amd64

docker compose -f docker/docker-compose.yml up -d --wait iris >/dev/null
for pair in aarch64:linux/arm64 x86_64:linux/amd64; do
  arch=${pair%%:*}; platform=${pair#*:}
  echo "### Linux smoke test $arch"
  docker build -q --platform "$platform" -t "irisfs-linux-clean:$arch" docker/linux-clean >/dev/null
  docker run --rm --platform "$platform" --network irisfs_default --device /dev/fuse --cap-add SYS_ADMIN \
    --security-opt apparmor:unconfined -v "$PWD:/work" -w /work "irisfs-linux-clean:$arch" \
    sh scripts/smoke-binary.sh "dist/valhallisc-linux-$arch" iris | tail -1
done
curl -s -o /dev/null -u _SYSTEM:irisfs-test -X DELETE http://localhost:52773/api/atelier/v8/USER/doc/Demo.SmokeBin.cls || true

cp "dist/ValhallISC-${VERSION}-macos-arm64.dmg" "dist/ValhallISC-${VERSION}-macos-x86_64.dmg" "$OUT/"
for arch in x86_64 aarch64; do cp "dist/valhallisc-linux-$arch" "$OUT/valhallisc-${VERSION}-linux-$arch"; done
(cd "$OUT" && shasum -a 256 * > SHA256SUMS && cat SHA256SUMS)
echo "release ${VERSION} ($(git rev-parse --short HEAD)) in $OUT"
