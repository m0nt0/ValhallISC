"""Phase 7 end-to-end: MountManager driving real worker processes and real FUSE mounts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from irisfs.config.profile import Profile
from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import ProfileStore
from irisfs.mount.manager import Change, MountManager, State, irisfs_mounts
from irisfs.mount.unmount import is_mounted
from tests.conftest import ROOT, IrisConn

pytestmark = [pytest.mark.iris, pytest.mark.fuse]


class Watch:
    def __init__(self) -> None:
        self.changes: list[Change] = []
        self.cond = threading.Condition()

    def __call__(self, c: Change) -> None:
        with self.cond:
            self.changes.append(c)
            self.cond.notify_all()

    def wait(self, pid: str, state: State, timeout: float = 60, after: int = 0) -> Change:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for c in self.changes[after:]:
                    if c.profile_id == pid and c.state is state:
                        return c
                assert time.monotonic() < deadline, f"no {state}: {self.changes}"
                self.cond.wait(0.1)


@pytest.fixture
def env(
    tmp_path: Path, iris_conn: IrisConn, fuse_available: None
) -> Iterator[tuple[ProfileStore, MountManager, Watch]]:
    store = ProfileStore(tmp_path / "profiles.json", secrets=FileSecretStore(tmp_path / "secrets.json"))
    worker_env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    mgr = MountManager(store, env=worker_env, stop_timeout=5)
    watch = Watch()
    mgr.subscribe(watch)
    yield store, mgr, watch
    mgr.unmount_all(force=True, wait=20)


def profile(store: ProfileStore, iris: IrisConn, tmp_path: Path, name: str, **kw: object) -> Profile:
    d = tmp_path / f"mnt-{name}"
    d.mkdir()
    user = str(kw.pop("username", iris.user))
    password = str(kw.pop("password", iris.password))
    p = Profile(name=name, host=iris.host, port=iris.port, username=user, mount_point=str(d), **kw)  # type: ignore[arg-type]
    return store.add(p, password=password)


def test_two_profiles_at_once_then_unmount_all(
    env: tuple[ProfileStore, MountManager, Watch], iris_conn: IrisConn, tmp_path: Path
) -> None:
    store, mgr, watch = env
    rw = profile(store, iris_conn, tmp_path, "rw")
    ro = profile(store, iris_conn, tmp_path, "ro", read_only=True)
    mgr.mount(rw.id)
    mgr.mount(ro.id)
    watch.wait(rw.id, State.ACTIVE)
    watch.wait(ro.id, State.ACTIVE)
    assert (Path(rw.mount_point) / "USER/Demo/Person.cls.xml").read_bytes().startswith(b"<?xml")
    assert (Path(ro.mount_point) / "TESTNS/Test/A.cls.xml").read_bytes().startswith(b"<?xml")
    assert mgr.unmount_all(wait=30) == []
    assert not is_mounted(rw.mount_point) and not is_mounted(ro.mount_point)
    assert mgr.state(rw.id) is State.INACTIVE and mgr.state(ro.id) is State.INACTIVE


def test_wrong_password_reported(
    env: tuple[ProfileStore, MountManager, Watch], iris_conn: IrisConn, tmp_path: Path
) -> None:
    store, mgr, watch = env
    p = profile(store, iris_conn, tmp_path, "badpw", password="wrong")
    mgr.mount(p.id)
    watch.wait(p.id, State.INACTIVE)
    assert any(c.event and c.event.get("code") == "AUTH_FAILED" for c in watch.changes)
    assert not is_mounted(p.mount_point)


def test_killed_worker_is_detected_and_cleaned(
    env: tuple[ProfileStore, MountManager, Watch], iris_conn: IrisConn, tmp_path: Path
) -> None:
    store, mgr, watch = env
    p = profile(store, iris_conn, tmp_path, "victim")
    mgr.mount(p.id)
    watch.wait(p.id, State.ACTIVE)
    pid = mgr._mounts[p.id].proc.pid
    os.kill(pid, signal.SIGKILL)
    change = watch.wait(p.id, State.INACTIVE, after=0)
    assert change.message and "unexpectedly" in change.message
    deadline = time.monotonic() + 15
    while is_mounted(p.mount_point) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not is_mounted(p.mount_point)


def test_stale_mount_cleaned_on_startup(tmp_path: Path, iris_conn: IrisConn, fuse_available: None) -> None:
    store = ProfileStore(tmp_path / "profiles.json", secrets=FileSecretStore(tmp_path / "secrets.json"))
    p = profile(store, iris_conn, tmp_path, "stale")
    # A worker from a "previous session", started outside any manager, then killed hard.
    proc = subprocess.Popen(
        [sys.executable, "-m", "irisfs", "worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(
        json.dumps({"cmd": "start", "profile": p.to_dict(), "password": iris_conn.password}) + "\n"
    )
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["event"] == "mounted"
    proc.kill()
    proc.wait()
    mgr = MountManager(store)
    mgr.cleanup_stale()  # on Linux libfuse3's auto_unmount may already have removed it
    assert not is_mounted(p.mount_point)
    assert os.path.realpath(p.mount_point) not in irisfs_mounts()


def test_busy_unmount_needs_force(
    env: tuple[ProfileStore, MountManager, Watch], iris_conn: IrisConn, tmp_path: Path
) -> None:
    store, mgr, watch = env
    p = profile(store, iris_conn, tmp_path, "busy")
    mgr.mount(p.id)
    watch.wait(p.id, State.ACTIVE)
    holder = subprocess.Popen(["sleep", "60"], cwd=Path(p.mount_point) / "USER")
    try:
        time.sleep(0.5)
        n = len(watch.changes)
        mgr.unmount(p.id)
        busy = watch.wait(p.id, State.ACTIVE, after=n, timeout=20)
        assert busy.event and busy.event.get("code") == "BUSY"
        assert is_mounted(p.mount_point)
        mgr.unmount(p.id, force=True)
        watch.wait(p.id, State.INACTIVE, after=n, timeout=30)
        deadline = time.monotonic() + 10
        while is_mounted(p.mount_point) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not is_mounted(p.mount_point)
    finally:
        holder.kill()
        holder.wait()
