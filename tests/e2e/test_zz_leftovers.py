"""Runs last in the e2e suite (file name order): nothing the tests created may be left mounted or running."""

import os
import subprocess
import tempfile
import time

import pytest

from irisfs.mount.manager import irisfs_mounts

pytestmark = [pytest.mark.fuse]


def _workers() -> list[str]:
    # Anchored: `python -m irisfs worker` or a frozen `.../irisfs worker` - not shells that merely mention it.
    pattern = r"(-m irisfs|/irisfs|/ValhallISC) worker$"  # python, Linux binary, macOS app
    out = subprocess.run(["pgrep", "-fl", pattern], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def test_no_leftover_mounts_or_workers(fuse_available: None) -> None:
    tmp = os.path.realpath(tempfile.gettempdir())
    # Test mounts live under the temp dir; a manual mount elsewhere (e.g. ~/IRISFS-test) is not ours.
    mounts = [m for m in irisfs_mounts() if m.startswith(tmp) or m.startswith("/tmp/")]
    assert mounts == [], f"leftover irisfs test mounts: {mounts}"
    deadline = time.monotonic() + 10  # a worker may still be finishing its shutdown
    while _workers() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert _workers() == [], f"leftover worker processes: {_workers()}"
