"""G11 end-to-end: the headless CLI drives real (detached) mounts, and interoperates with the tray manager."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import ProfileStore
from irisfs.mount.manager import Change, MountManager, State
from irisfs.mount.unmount import is_mounted
from tests.conftest import ROOT, IrisConn
from tests.integration.xmlsamples import class_xml

pytestmark = [pytest.mark.iris, pytest.mark.fuse]


class Cli:
    def __init__(self, config_dir: Path) -> None:
        self.env = dict(
            os.environ,
            PYTHONPATH=str(ROOT / "src"),
            VALHALLISC_CONFIG_DIR=str(config_dir),
            VALHALLISC_SECRETS="file",
        )
        self.env.pop("VALHALLISC_PASSWORD", None)
        binary = os.environ.get("IRISFS_BIN")
        self.base = [binary] if binary else [sys.executable, "-m", "irisfs"]
        self.config_dir = config_dir

    def __call__(
        self, *argv: str, stdin: str | None = None, timeout: float = 90
    ) -> tuple[int, dict[str, Any]]:
        r = subprocess.run(
            [*self.base, "--batch", *argv],
            input=stdin,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=timeout,
        )
        lines = r.stdout.strip().splitlines()
        return r.returncode, (json.loads(lines[-1]) if lines else {"stderr": r.stderr})

    def store(self) -> ProfileStore:
        return ProfileStore(
            self.config_dir / "profiles.json", secrets=FileSecretStore(self.config_dir / "secrets.json")
        )


@pytest.fixture
def cli(tmp_path: Path, iris_conn: IrisConn, fuse_available: None) -> Iterator[Cli]:
    c = Cli(tmp_path / "cfg")
    yield c
    _, data = c("status")  # disconnect whatever a failed test left behind
    for row in data.get("profiles", []):
        if row.get("connected"):
            c("disconnect", row["profile"], "--force")


def create(cli: Cli, iris: IrisConn, tmp_path: Path, name: str, password: str | None = None) -> Path:
    mnt = tmp_path / f"mnt-{name}"
    code, data = cli(
        "--create-profile",
        name,
        "--host",
        iris.host,
        "--port",
        str(iris.port),
        "--user",
        iris.user,
        "--mountpoint",
        str(mnt),
        "--password-stdin",
        stdin=(password or iris.password) + "\n",
    )
    assert code == 0, data
    return mnt


def test_full_lifecycle(cli: Cli, iris_conn: IrisConn, tmp_path: Path) -> None:
    mnt = create(cli, iris_conn, tmp_path, "life")
    assert cli("--test", "life")[0] == 0
    started = time.monotonic()
    code, data = cli("--connect", "life")
    assert code == 0 and data["mountpoint"] == str(mnt), data
    assert time.monotonic() - started < 30
    # the CLI process has exited; the detached worker keeps the mount alive
    assert "USER" in os.listdir(mnt)
    assert (mnt / "USER/Demo/Person.cls.xml").read_bytes().startswith(b"<?xml")
    (tmp_path / "cli.xml").write_bytes(class_xml("Demo.CliImported"))
    subprocess.run(["cp", str(tmp_path / "cli.xml"), str(mnt / "USER")], check=True)
    deadline = time.monotonic() + 15
    while not (mnt / "USER/Demo/CliImported.cls.xml").exists():
        assert time.monotonic() < deadline, "import through a CLI mount did not show up"
        time.sleep(0.3)
    code, data = cli("--status", "life")
    assert data["profiles"][0]["connected"] and data["profiles"][0]["owner"] == "cli"
    assert cli("--connect", "life")[1].get("already") is True  # idempotent
    assert cli("--delete-profile", "life")[0] == 6  # connected
    code, data = cli("--disconnect", "life")
    assert code == 0, data
    assert not is_mounted(str(mnt))
    assert cli("--status", "life")[1]["profiles"][0]["connected"] is False
    assert cli("--delete-profile", "life")[0] == 0
    # clean up the imported class
    from irisfs.atelier.client import AtelierClient

    with AtelierClient(iris_conn.base_url, iris_conn.user, iris_conn.password) as c:
        c.delete_doc("USER", "Demo.CliImported.cls")


def test_wrong_password_exit_3(cli: Cli, iris_conn: IrisConn, tmp_path: Path) -> None:
    mnt = create(cli, iris_conn, tmp_path, "badpw", password="wrong")
    code, data = cli("--connect", "badpw")
    assert code == 3 and data["error"] == "AUTH_FAILED"
    assert not is_mounted(str(mnt))
    assert cli("--status", "badpw")[1]["profiles"][0]["connected"] is False


@pytest.mark.skipif(sys.platform == "win32", reason="busy semantics differ on Windows")
def test_busy_disconnect_needs_force(cli: Cli, iris_conn: IrisConn, tmp_path: Path) -> None:
    mnt = create(cli, iris_conn, tmp_path, "busy")
    assert cli("--connect", "busy")[0] == 0
    holder = subprocess.Popen(["sleep", "60"], cwd=mnt / "USER")
    try:
        time.sleep(0.5)
        code, data = cli("--disconnect", "busy")
        assert code == 6 and data["error"] == "BUSY"
        assert is_mounted(str(mnt))
        assert cli("--disconnect", "busy", "--force")[0] == 0
        deadline = time.monotonic() + 10
        while is_mounted(str(mnt)) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not is_mounted(str(mnt))
    finally:
        holder.kill()
        holder.wait()


class Watch:
    def __init__(self) -> None:
        self.changes: list[Change] = []
        self.cond = threading.Condition()

    def __call__(self, c: Change) -> None:
        with self.cond:
            self.changes.append(c)
            self.cond.notify_all()

    def wait(self, pid: str, state: State, after: int = 0, timeout: float = 60) -> Change:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for c in self.changes[after:]:
                    if c.profile_id == pid and c.state is state:
                        return c
                assert time.monotonic() < deadline, f"no {state}: {self.changes}"
                self.cond.wait(0.1)


def test_tray_sees_and_unmounts_cli_mount(cli: Cli, iris_conn: IrisConn, tmp_path: Path) -> None:
    mnt = create(cli, iris_conn, tmp_path, "shared")
    assert cli("--connect", "shared")[0] == 0
    store = cli.store()
    pid = store.find_by_name("shared").id  # type: ignore[union-attr]
    mgr = MountManager(store, env=cli.env)
    assert mgr.state(pid) is State.ACTIVE and mgr.external(pid) is not None
    assert mgr.cleanup_stale() == []  # a live CLI mount is not stale
    assert is_mounted(str(mnt))
    watch = Watch()
    mgr.subscribe(watch)
    mgr.unmount(pid)
    watch.wait(pid, State.INACTIVE)
    assert not is_mounted(str(mnt))
    assert mgr.state(pid) is State.INACTIVE


def test_cli_disconnects_tray_mount_without_error(cli: Cli, iris_conn: IrisConn, tmp_path: Path) -> None:
    mnt = create(cli, iris_conn, tmp_path, "traymount")
    store = cli.store()
    pid = store.find_by_name("traymount").id  # type: ignore[union-attr]
    mgr = MountManager(store, env=cli.env)
    watch = Watch()
    mgr.subscribe(watch)
    mnt.mkdir()  # the tray's controller creates the folder before mounting
    mgr.mount(pid)
    watch.wait(pid, State.ACTIVE)
    _, data = cli("--status", "traymount")
    assert data["profiles"][0]["connected"] and data["profiles"][0]["owner"] == "tray"
    assert cli("--disconnect", "traymount")[0] == 0
    change = watch.wait(pid, State.INACTIVE)
    assert change.message is None  # a clean external unmount is not reported as a crash
    assert not is_mounted(str(mnt))
