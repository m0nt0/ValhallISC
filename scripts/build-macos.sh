#!/usr/bin/env bash
# Build dist/macos-<arch>/ValhallISC.app (+ zip) and dist/macos-<arch>/valhallisc (CLI) on macOS.
# The architecture is the Python's: Apple silicon natively; Intel with an x86_64 Python under Rosetta:
#   arch -x86_64 env PYTHON=.venv-x86_64/bin/python scripts/build-macos.sh      (see doc/RELEASING.md)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
ARCH=$("$PY" -c 'import platform; print(platform.machine())')
OUT="dist/macos-${ARCH}"
source scripts/_build_info.sh
rm -rf "$OUT"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$OUT" --workpath "build/macos-${ARCH}" packaging/valhallisc.spec
rm -rf "$OUT/ValhallISC-onedir"   # intermediate onedir folder; the .app bundle contains it
# guard: the FUSE layer must never be bundled (see packaging/valhallisc.spec)
if find "$OUT/ValhallISC.app" -iname '*fuse*' -o -iname '*MFMount*' -o -iname 'libswiftCompatibility*' | grep -q .; then
  echo "ERROR: FUSE libraries were bundled into the app" >&2; exit 1
fi
# CLI wrapper: runs the bundle's executable (a onefile binary would start in 10-100 s on macOS, ADR-009)
cat > "$OUT/valhallisc" <<'SH'
#!/bin/sh
# ValhallISC command line: valhallisc doctor | mount | unmount | profiles ...
here=$(cd "$(dirname "$0")" && pwd)
for app in "$here/ValhallISC.app" /Applications/ValhallISC.app "$HOME/Applications/ValhallISC.app"; do
  [ -x "$app/Contents/MacOS/ValhallISC" ] && exec "$app/Contents/MacOS/ValhallISC" "$@"
done
echo "ValhallISC.app not found next to this script or in /Applications" >&2
exit 1
SH
chmod +x "$OUT/valhallisc"
(cd "$OUT" && ditto -c -k --keepParent ValhallISC.app "ValhallISC-macos-${ARCH}.zip")
lipo -archs "$OUT/ValhallISC.app/Contents/MacOS/ValhallISC"
ls -la "$OUT"
