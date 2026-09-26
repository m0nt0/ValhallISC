from __future__ import annotations

import platform
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from irisfs.config.profile import Profile
from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import ProfileActiveError, ProfileStore
from irisfs.mount.manager import Change, MountError, MountManager, State
from irisfs.mount.unmount import UnmountResult

FAKE = [sys.executable, str(Path(__file__).resolve().parent.parent / "fakes" / "fake_worker.py")]


class Recorder:
    def __init__(self) -> None:
        self.changes: list[Change] = []
        self.cond = threading.Condition()

    def __call__(self, change: Change) -> None:
        with self.cond:
            self.changes.append(change)
            self.cond.notify_all()

    def wait_state(self, pid: str, state: State, timeout: float = 10) -> Change:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for c in self.changes:
                    if c.profile_id == pid and c.state is state:
                        return c
                if time.monotonic() > deadline:
                    raise AssertionError(f"no {state} for {pid}: {self.changes}")
                self.cond.wait(0.05)

    def states(self, pid: str) -> list[State]:
        with self.cond:
            return [c.state for c in self.changes if c.profile_id == pid]


@pytest.fixture
def setup(tmp_path: Path) -> tuple[ProfileStore, MountManager, Recorder, list[tuple[str, bool]]]:
    system = platform.system()  # tmp_path follows the real platform's path rules
    store = ProfileStore(tmp_path / "p.json", secrets=FileSecretStore(tmp_path / "s.json"), system=system)
    unmounts: list[tuple[str, bool]] = []

    def fake_unmount(path: str, system: str, *, force: bool = False, timeout: float = 15) -> UnmountResult:
        unmounts.append((path, force))
        return UnmountResult(True, False, "")

    mgr = MountManager(
        store, worker_command=FAKE, system=system, mount_timeout=2, stop_timeout=1, unmount_fn=fake_unmount
    )
    rec = Recorder()
    mgr.subscribe(rec)
    return store, mgr, rec, unmounts


def add(store: ProfileStore, tmp_path: Path, scenario: str, sub: str | None = None) -> Profile:
    d = tmp_path / (sub or scenario)
    d.mkdir()
    name = f"{scenario}:{sub}" if sub else scenario
    return store.add(Profile(name=name, host="h", username="u", mount_point=str(d)), password="pw")


def test_mount_and_unmount(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "ok")
    mgr.mount(p.id)
    assert mgr.state(p.id) in (State.MOUNTING, State.ACTIVE)
    rec.wait_state(p.id, State.ACTIVE)
    assert mgr.is_active(p.id)
    mgr.unmount(p.id)
    rec.wait_state(p.id, State.INACTIVE)
    assert rec.states(p.id) == [State.MOUNTING, State.ACTIVE, State.UNMOUNTING, State.INACTIVE]
    assert not mgr.is_active(p.id)


def test_auth_failure_goes_inactive_with_error(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "auth_fail")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.INACTIVE)
    errors = [c for c in rec.changes if c.event and c.event.get("code") == "AUTH_FAILED"]
    assert errors and errors[0].message == "bad password"


def test_mount_timeout_kills_worker(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "hang")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.INACTIVE, timeout=10)
    assert any(c.event and c.event.get("code") == "TIMEOUT" for c in rec.changes)


def test_crash_while_active_notifies(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "crash")
    mgr.mount(p.id)
    change = rec.wait_state(p.id, State.INACTIVE)
    assert change.message and "stopped unexpectedly" in change.message


def test_busy_unmount_then_force(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "busy")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.ACTIVE)
    mgr.unmount(p.id)
    rec.wait_state(p.id, State.UNMOUNTING)
    deadline = time.monotonic() + 5
    while not any(c.event and c.event.get("code") == "BUSY" for c in rec.changes):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    time.sleep(0.2)
    assert mgr.state(p.id) is State.ACTIVE  # still mounted
    mgr.unmount(p.id, force=True)
    rec.wait_state(p.id, State.INACTIVE)


def test_stop_ignored_escalates_to_os_unmount_and_kill(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, unmounts = setup
    p = add(store, tmp_path, "ignore_stop")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.ACTIVE)
    mgr.unmount(p.id)
    rec.wait_state(p.id, State.INACTIVE, timeout=10)
    assert (p.mount_point, True) in unmounts


def test_same_profile_twice_rejected(setup: Any, tmp_path: Path) -> None:
    store, mgr, _rec, _ = setup
    p = add(store, tmp_path, "ok")
    mgr.mount(p.id)
    with pytest.raises(MountError):
        mgr.mount(p.id)
    mgr.unmount_all(wait=10)


def test_overlapping_mount_points_rejected(setup: Any, tmp_path: Path) -> None:
    store, mgr, _rec, _ = setup
    a = add(store, tmp_path, "ok")
    b = store.add(Profile(name="other", host="h", username="u", mount_point=a.mount_point), password="pw")
    mgr.mount(a.id)
    with pytest.raises(MountError, match="overlaps"):
        mgr.mount(b.id)
    mgr.unmount_all(wait=10)


def test_active_profile_cannot_be_edited_or_deleted(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "ok")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.ACTIVE)
    with pytest.raises(ProfileActiveError):
        store.delete(p.id)
    with pytest.raises(ProfileActiveError):
        store.update(p)
    mgr.unmount_all(wait=10)
    store.delete(p.id)


def test_unmount_all_in_parallel(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    ids = [add(store, tmp_path, "ok", sub=f"m{i}").id for i in range(3)]
    for pid in ids:
        mgr.mount(pid)
    for pid in ids:
        rec.wait_state(pid, State.ACTIVE)
    started = time.monotonic()
    still = mgr.unmount_all(wait=10)
    assert still == [] and time.monotonic() - started < 5
    assert all(mgr.state(pid) is State.INACTIVE for pid in ids)


def test_unmount_all_reports_stuck_mounts(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup
    p = add(store, tmp_path, "busy")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.ACTIVE)
    assert mgr.unmount_all(wait=1.5) == ["busy"]
    assert mgr.unmount_all(force=True, wait=10) == []


def test_subscriber_exceptions_do_not_break_manager(setup: Any, tmp_path: Path) -> None:
    store, mgr, rec, _ = setup

    def bad(change: Change) -> None:
        raise RuntimeError("boom")

    mgr.subscribe(bad)
    p = add(store, tmp_path, "ok")
    mgr.mount(p.id)
    rec.wait_state(p.id, State.ACTIVE)
    mgr.unmount_all(wait=10)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no mount table (os.path.ismount)")
def test_irisfs_mount_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from irisfs.mount import manager, unmount

    out = (
        "/dev/disk3s1 on / (apfs, local, journaled)\n"
        "irisfs-adhoc on /Users/me/IRIS test (macfuse, local, nodev)\n"
        "irisfs-Prod on /mnt/iris type fuse.irisfs-Prod (rw,nosuid)\n"
        "other-fs on /mnt/x type fuse (rw)\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    monkeypatch.setattr(unmount, "canonical", lambda p: p)
    assert manager.irisfs_mounts() == ["/Users/me/IRIS test", "/mnt/iris"]
    assert unmount.is_mounted("/mnt/x") and not unmount.is_mounted("/mnt/y")
