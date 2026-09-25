#!/usr/bin/env bash
# Build dist/ValhallISC.app (+ zip) and dist/valhallisc (CLI) on macOS.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
source scripts/_build_info.sh
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/valhallisc.spec
rm -rf dist/ValhallISC-onedir   # intermediate onedir folder; the .app bundle contains it
# CLI wrapper: runs the bundle's executable (a onefile binary would start in 10-100 s on macOS, ADR-009)
cat > dist/valhallisc <<'SH'
#!/bin/sh
# ValhallISC command line: valhallisc doctor | mount | unmount | profiles ...
here=$(cd "$(dirname "$0")" && pwd)
for app in "$here/ValhallISC.app" /Applications/ValhallISC.app "$HOME/Applications/ValhallISC.app"; do
  [ -x "$app/Contents/MacOS/ValhallISC" ] && exec "$app/Contents/MacOS/ValhallISC" "$@"
done
echo "ValhallISC.app not found next to this script or in /Applications" >&2
exit 1
SH
chmod +x dist/valhallisc
(cd dist && ditto -c -k --keepParent ValhallISC.app ValhallISC-macos-$(uname -m).zip)
ls -la dist
