#!/usr/bin/env bash
# Run scripts/test.sh inside the Linux test container (FUSE + Xvfb), against the IRIS container.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f docker/docker-compose.yml up -d --wait iris >/dev/null
exec docker compose -f docker/docker-compose.yml run --rm -T linux-test \
  xvfb-run -a env PYTHON=/opt/venv/bin/python scripts/test.sh "$@"
