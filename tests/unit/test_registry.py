import os
import subprocess
import sys
from pathlib import Path

from irisfs.mount import registry


def test_write_get_remove(tmp_path: Path) -> None:
    e = registry.new_entry("p1", "Dev", "/mnt/dev", "cli")
    registry.write(tmp_path, e)
    assert registry.get(tmp_path, "p1") == e
    registry.remove(tmp_path, "p1", pid=e.pid + 1)  # someone else's entry: kept
    assert registry.get(tmp_path, "p1") is not None
    registry.remove(tmp_path, "p1", pid=e.pid)
    assert registry.get(tmp_path, "p1") is None


def test_dead_worker_entries_are_dropped(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead = registry.MountEntry("p2", "Old", "/mnt/old", proc.pid, "cli", 0.0)
    registry.write(tmp_path, dead)
    assert registry.entries(tmp_path) == {}
    assert not (tmp_path / "p2.json").exists()


def test_garbage_files_ignored(tmp_path: Path) -> None:
    (tmp_path / "junk.json").write_text("{not json")
    assert registry.entries(tmp_path) == {}
    assert registry.entries(tmp_path / "missing") == {}


def test_pid_alive() -> None:
    assert registry.pid_alive(os.getpid())
    assert not registry.pid_alive(0)
