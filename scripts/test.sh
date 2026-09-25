#!/usr/bin/env bash
# Usage: scripts/test.sh [lint|unit|integration|e2e|gui|all] ...
# Gate runs: all suites with IRISFS_REQUIRE_* so missing IRIS/FUSE fails instead of skipping.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 )}
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
[ $# -eq 0 ] && set -- all

# pytest exit code 5 = "no tests collected": allowed while a suite is still empty (early phases).
pt() { local rc=0; "$PY" -m pytest -q "$@" || rc=$?; [ $rc -eq 5 ] && { echo "(no tests yet)"; return 0; }; return $rc; }

run_suite() {
  case "$1" in
    lint)
      "$PY" -m ruff check . && "$PY" -m ruff format --check . && "$PY" -m mypy ;;
    unit)        pt tests/unit ;;
    integration) IRISFS_REQUIRE_IRIS=1 pt tests/integration ;;
    e2e)         IRISFS_REQUIRE_IRIS=1 IRISFS_REQUIRE_FUSE=1 pt tests/e2e ;;
    gui)         pt tests/gui ;;
    all)         for s in lint unit integration e2e gui; do run_suite "$s"; done ;;
    *) echo "unknown suite: $1" >&2; exit 2 ;;
  esac
}
for s in "$@"; do echo "=== $s ($(uname -s)) ==="; run_suite "$s"; done
