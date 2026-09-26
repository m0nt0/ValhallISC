#!/usr/bin/env bash
# Build the Linux binary in Docker (Ubuntu 24.04 base, distro wxPython).
#   scripts/build-linux.sh [amd64|arm64]     (default: this machine's architecture)
# Output: dist/valhallisc-linux-<x86_64|aarch64>
# Runtime needs glibc >= 2.39 (Ubuntu 24.04 / Debian 13 or newer) and fuse3; GTK3 for the tray app.
# A foreign architecture is built under emulation (Docker Desktop / binfmt + QEMU): slow but works.
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-$(uname -m)}" in
  amd64|x86_64)  PLATFORM=linux/amd64; ARCH=x86_64 ;;
  arm64|aarch64) PLATFORM=linux/arm64; ARCH=aarch64 ;;
  *) echo "unknown architecture: $1" >&2; exit 2 ;;
esac
source scripts/_build_info.sh
IMAGE="irisfs-linux-test:${ARCH}"
docker build -q --platform "$PLATFORM" -t "$IMAGE" docker/linux-test >/dev/null
docker run --rm --platform "$PLATFORM" -v "$PWD:/work" -w /work "$IMAGE" bash -c '
  set -e
  pip install -q "pyinstaller>=6" >/dev/null
  python -m PyInstaller --noconfirm --clean --distpath /tmp/dist --workpath /tmp/build packaging/valhallisc.spec \
    >/tmp/pyi.log 2>&1 || { tail -40 /tmp/pyi.log; exit 1; }
  mkdir -p dist && cp /tmp/dist/valhallisc "dist/valhallisc-linux-'"$ARCH"'"
  chown '"$(id -u):$(id -g)"' "dist/valhallisc-linux-'"$ARCH"'"
'
ls -la "dist/valhallisc-linux-${ARCH}"
