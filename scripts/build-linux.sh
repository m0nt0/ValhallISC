#!/usr/bin/env bash
# Build dist/valhallisc-linux-<arch> inside the linux-test image (Ubuntu 24.04, distro wxPython).
# The binary needs glibc >= 2.39 (Ubuntu 24.04 / Debian 13 or newer) plus fuse3 and GTK3 at runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_build_info.sh
docker compose -f docker/docker-compose.yml run --rm -T --no-deps linux-test bash -c '
  set -e
  pip install -q "pyinstaller>=6" >/dev/null
  python -m PyInstaller --noconfirm --clean --distpath /tmp/dist --workpath /tmp/build packaging/valhallisc.spec
  mkdir -p dist && cp /tmp/dist/valhallisc "dist/valhallisc-linux-$(uname -m)"
  chown -R '"$(id -u):$(id -g)"' dist
'
ls -la dist
