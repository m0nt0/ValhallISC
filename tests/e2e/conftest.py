"""Real mounts: a worker subprocess (the same code path the GUI uses) serving the IRIS test container."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from irisfs.mount.unmount import is_mounted, unmount
from tests.conftest import ROOT, IrisConn

SYSTEM = platform.system()


class MountedFs:
    def __init__(self, proc: subprocess.Popen[str], mountpoint: Path) -> None:
        self.proc = proc
        self.path = mountpoint
        self.events: list[dict[str, Any]] = []
        self._cond = threading.Condition()
        threading.Thread(target=self._read_events, daemon=True).start()

    def _read_events(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            with self._cond:
                self.events.append(event)
                self._cond.notify_all()

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for e in self.events:
                    if predicate(e):
                        return e
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (self.proc.poll() is not None and not self._pending()):
                    raise TimeoutError(f"no matching event; got {self.events}")
                self._cond.wait(min(remaining, 0.2))

    def _pending(self) -> bool:
        return bool(self.proc.stdout and not self.proc.stdout.closed)

    def event_count(self) -> int:
        with self._cond:
            return len(self.events)

    def next_event(self, after: int, timeout: float = 30) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while len(self.events) <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no new event after #{after}; got {self.events}")
                self._cond.wait(min(remaining, 0.2))
            return self.events[after]

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def stop(self, timeout: float = 15) -> int:
        if self.proc.poll() is None:
            with contextlib.suppress(OSError):
                self.send({"cmd": "stop"})
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                unmount(str(self.path), SYSTEM, force=True)
                self.proc.kill()
                self.proc.wait(5)
        if is_mounted(str(self.path)):
            unmount(str(self.path), SYSTEM, force=True)
        return self.proc.returncode


def start_worker(profile: dict[str, Any], password: str) -> subprocess.Popen[str]:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    cmd = (
        [os.environ["IRISFS_BIN"], "worker"]
        if os.environ.get("IRISFS_BIN")
        else [sys.executable, "-m", "irisfs", "worker"]
    )
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, text=True, env=env, cwd=str(ROOT)
    )
    assert proc.stdin is not None
    proc.stdin.write(
        json.dumps({"cmd": "start", "profile": profile, "password": password, "tree_ttl": 2}) + "\n"
    )
    proc.stdin.flush()
    return proc


def profile_dict(iris: IrisConn, mountpoint: Path, **overrides: Any) -> dict[str, Any]:
    p = {
        "name": "e2e",
        "host": iris.host,
        "port": iris.port,
        "username": iris.user,
        "mount_point": str(mountpoint),
    }
    p.update(overrides)
    return p


@pytest.fixture
def mount_factory(
    tmp_path_factory: pytest.TempPathFactory, iris_conn: IrisConn, fuse_available: None
) -> Iterator[Callable[..., MountedFs]]:
    mounted: list[MountedFs] = []

    def factory(*, password: str | None = None, expect_mount: bool = True, **overrides: Any) -> MountedFs:
        mp = Path(overrides.pop("mount_point", None) or tmp_path_factory.mktemp("mnt"))
        proc = start_worker(
            profile_dict(iris_conn, mp, **overrides), iris_conn.password if password is None else password
        )
        m = MountedFs(proc, mp)
        mounted.append(m)
        if expect_mount:
            m.wait_for(lambda e: e.get("event") in ("mounted", "error"), timeout=60)
            if not any(e.get("event") == "mounted" for e in m.events):
                raise AssertionError(f"mount failed: {m.events}")
            deadline = time.monotonic() + 10
            while not is_mounted(str(mp)) and time.monotonic() < deadline:
                time.sleep(0.05)
        return m

    yield factory
    for m in mounted:
        m.stop()


@pytest.fixture
def mnt(mount_factory: Callable[..., MountedFs]) -> MountedFs:
    return mount_factory()


class TcpProxy:
    """Forwards localhost:<port> to IRIS; `down()` drops all connections and refuses new ones."""

    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(64)
        self.port = self.server.getsockname()[1]
        self.enabled = True
        self._conns: list[socket.socket] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            if not self.enabled:
                client.close()
                continue
            try:
                upstream = socket.create_connection(self.target, timeout=5)
            except OSError:
                client.close()
                continue
            with self._lock:
                self._conns += [client, upstream]
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()

    @staticmethod
    def _pipe(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                with contextlib.suppress(OSError):
                    s.close()

    def down(self) -> None:
        self.enabled = False
        with self._lock:
            for c in self._conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self._conns.clear()

    def up(self) -> None:
        self.enabled = True

    def close(self) -> None:
        self.down()
        self.server.close()


@pytest.fixture
def proxy(iris_conn: IrisConn) -> Iterator[TcpProxy]:
    p = TcpProxy((iris_conn.host, iris_conn.port))
    yield p
    p.close()
