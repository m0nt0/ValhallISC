"""Plan G5 performance check: cold listing of a namespace with 2000 classes (PERFNS)."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

import pytest

from tests.e2e.conftest import MountedFs

pytestmark = [pytest.mark.iris, pytest.mark.fuse, pytest.mark.perf]


def timed(cmd: list[str]) -> tuple[float, str]:
    started = time.perf_counter()
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return time.perf_counter() - started, out


def test_perf_listing(mount_factory: Callable[..., MountedFs]) -> None:
    m = mount_factory()
    t_ls, out = timed(["ls", "-R", str(m.path / "PERFNS")])
    files = [line for line in out.splitlines() if line.endswith(".cls.xml")]
    assert len(files) == 2000
    m.stop()

    m = mount_factory()  # fresh worker => cold caches
    t_lsl, out = timed(["ls", "-lR", str(m.path / "PERFNS")])
    assert out.count(".cls.xml") == 2000
    t_warm, _ = timed(["ls", "-lR", str(m.path / "PERFNS")])
    print(f"\nPERF ls -R: {t_ls:.2f}s   ls -lR cold: {t_lsl:.2f}s   ls -lR warm: {t_warm:.2f}s")
    assert t_ls < 5, f"ls -R took {t_ls:.1f}s (target < 5s)"
    assert t_lsl < 60, f"ls -lR took {t_lsl:.1f}s (target < 60s)"
