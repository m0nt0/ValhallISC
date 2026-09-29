"""AppController: tray and Profiles-window behaviour, without wx or real mounts."""

from __future__ import annotations

import platform
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from irisfs.config.profile import Profile
from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import ProfileStore
from irisfs.gui.controller import AppController, ProfileItem
from irisfs.mount import fuselib
from irisfs.mount.manager import Change, MountError, State


class FakePrompter:
    def __init__(self) -> None:
        self.answers: list[bool] = []  # consumed by confirm(); default True
        self.log: list[tuple[str, str, str]] = []

    def confirm(self, title: str, message: str, *, yes: str = "Yes", no: str = "No") -> bool:
        self.log.append(("confirm", title, message))
        return self.answers.pop(0) if self.answers else True

    def error(self, title: str, message: str) -> None:
        self.log.append(("error", title, message))

    def info(self, title: str, message: str) -> None:
        self.log.append(("info", title, message))

    def notify(self, title: str, message: str) -> None:
        self.log.append(("notify", title, message))

    def kinds(self) -> list[str]:
        return [k for k, _, _ in self.log]


class FakeManager:
    def __init__(self) -> None:
        self.states: dict[str, State] = {}
        self.calls: list[tuple[str, Any]] = []
        self.subscribers: list[Callable[[Change], None]] = []
        self.still_mounted: list[list[str]] = []  # results for successive unmount_all calls
        self.mount_error: str | None = None

    def subscribe(self, cb: Callable[[Change], None]) -> None:
        self.subscribers.append(cb)

    def state(self, pid: str) -> State:
        return self.states.get(pid, State.INACTIVE)

    def is_active(self, pid: str) -> bool:
        return self.state(pid) is not State.INACTIVE

    def external(self, pid: str) -> None:
        return None

    def mount(self, pid: str) -> None:
        if self.mount_error:
            raise MountError(self.mount_error)
        self.calls.append(("mount", pid))
        self.states[pid] = State.MOUNTING

    def unmount(self, pid: str, *, force: bool = False) -> None:
        self.calls.append(("unmount", (pid, force)))

    def unmount_all(self, *, force: bool = False, wait: float | None = None) -> list[str]:
        self.calls.append(("unmount_all", force))
        return self.still_mounted.pop(0) if self.still_mounted else []

    def publish(self, change: Change) -> None:
        for cb in self.subscribers:
            cb(change)


class Env:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(fuselib, "find_library", lambda system=None: fuselib.FuseLibrary("libfuse3", "x"))
        self.store = ProfileStore(
            tmp_path / "p.json", secrets=FileSecretStore(tmp_path / "s.json"), system=platform.system()
        )
        self.mgr = FakeManager()
        self.store.is_active = self.mgr.is_active
        self.prompter = FakePrompter()
        self.exited = 0
        self.refreshes = 0
        self.tmp = tmp_path
        self.ctl = AppController(
            self.store,
            self.mgr,  # type: ignore[arg-type]
            self.prompter,
            request_exit=self._exit,
            call_ui=lambda task: task(),
            run_bg=lambda task: task(),
            system="Linux",
        )
        self.ctl.add_listener(self._refresh)

    def _exit(self) -> None:
        self.exited += 1

    def _refresh(self) -> None:
        self.refreshes += 1

    def add(self, name: str = "Local", password: str | None = "pw") -> Profile:
        p = Profile(name=name, host="localhost", username="_SYSTEM", mount_point=str(self.tmp / "mnt" / name))
        return self.store.add(p, password=password)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(tmp_path, monkeypatch)


# ---- menu model ------------------------------------------------------------------------------
def test_profile_items_sorted_with_states(env: Env) -> None:
    b = env.add("beta")
    env.add("Alpha")
    env.mgr.states[b.id] = State.ACTIVE
    items = env.ctl.profile_items()
    assert [i.name for i in items] == ["Alpha", "beta"]
    assert items[1].active and not items[0].active


@pytest.mark.parametrize(
    ("state", "label", "enabled"),
    [
        (State.INACTIVE, "X", True),
        (State.ACTIVE, "X", True),
        (State.MOUNTING, "X (mounting…)", False),
        (State.UNMOUNTING, "X (unmounting…)", False),
    ],
)
def test_item_labels(state: State, label: str, enabled: bool) -> None:
    item = ProfileItem("id", "X", state, "/m")
    assert item.label == label and item.enabled is enabled


# ---- clicking profiles -----------------------------------------------------------------------
def test_click_inactive_mounts_and_creates_folder(env: Env) -> None:
    p = env.add()
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [("mount", p.id)]
    assert Path(p.mount_point).is_dir()
    assert env.refreshes >= 1


def test_click_active_confirm_yes_unmounts(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    env.prompter.answers = [True]
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [("unmount", (p.id, False))]
    assert "Unmount" in env.prompter.log[0][2]


def test_click_active_confirm_no_does_nothing(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    env.prompter.answers = [False]
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == []


def test_click_while_changing_state_is_ignored(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.MOUNTING
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [] and env.prompter.log == []


def test_mount_error_is_shown(env: Env) -> None:
    p = env.add()
    env.mgr.mount_error = "overlaps the mount point of active profile 'x'"
    env.ctl.on_profile_clicked(p.id)
    assert env.prompter.kinds() == ["error"] and "overlaps" in env.prompter.log[0][2]


def test_missing_password_is_explained(env: Env) -> None:
    p = env.add(password=None)
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [] and "No password" in env.prompter.log[0][2]


def test_missing_fuse_is_explained(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    p = env.add()
    monkeypatch.setattr(fuselib, "find_library", lambda system=None: None)
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [] and "FUSE" in env.prompter.log[0][2]


# ---- quit ------------------------------------------------------------------------------------
def test_quit_no_active_confirm(env: Env) -> None:
    env.prompter.answers = [True]
    env.ctl.on_quit()
    assert env.exited == 1 and env.mgr.calls == []


def test_quit_cancelled(env: Env) -> None:
    env.prompter.answers = [False]
    env.ctl.on_quit()
    assert env.exited == 0


def test_quit_unmounts_all_then_exits(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    env.prompter.answers = [True]
    env.ctl.on_quit()
    assert "Local" in env.prompter.log[0][2]  # lists what will be unmounted
    assert env.mgr.calls == [("unmount_all", False)] and env.exited == 1


def test_quit_busy_then_force(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    env.mgr.still_mounted = [["Local"], []]
    env.prompter.answers = [True, True]
    env.ctl.on_quit()
    assert env.mgr.calls == [("unmount_all", False), ("unmount_all", True)] and env.exited == 1


def test_quit_busy_then_cancel_keeps_running(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    env.mgr.still_mounted = [["Local"]]
    env.prompter.answers = [True, False]
    env.ctl.on_quit()
    assert env.exited == 0 and not env.ctl.quitting


# ---- manager events --------------------------------------------------------------------------
def test_busy_event_offers_force(env: Env) -> None:
    p = env.add()
    env.prompter.answers = [True]
    env.mgr.publish(Change(p.id, State.ACTIVE, {"event": "error", "code": "BUSY"}))
    assert env.mgr.calls == [("unmount", (p.id, True))]


def test_auth_error_shown_with_friendly_text(env: Env) -> None:
    p = env.add()
    env.mgr.publish(Change(p.id, State.MOUNTING, {"event": "error", "code": "AUTH_FAILED"}, "401"))
    kind, title, message = env.prompter.log[0]
    assert kind == "error" and "Local" in title and "user name or password" in message


def test_import_notifications(env: Env) -> None:
    p = env.add()
    env.mgr.publish(
        Change(p.id, State.ACTIVE, {"event": "imported", "items": ["A.cls"], "compile_errors": []})
    )
    env.mgr.publish(
        Change(p.id, State.ACTIVE, {"event": "imported", "items": ["B.cls"], "compile_errors": ["E1"]})
    )
    env.mgr.publish(Change(p.id, State.ACTIVE, {"event": "import_failed", "file": "x.xml", "message": "bad"}))
    titles = [t for k, t, _ in env.prompter.log if k == "notify"]
    assert titles == ["Local: imported", "Local: imported with compile errors", "Local: import failed"]


def test_worker_crash_reported(env: Env) -> None:
    p = env.add()
    env.mgr.publish(Change(p.id, State.INACTIVE, None, "The connection 'Local' stopped unexpectedly"))
    assert env.prompter.kinds() == ["error"]


def test_state_changes_refresh_ui(env: Env) -> None:
    p = env.add()
    before = env.refreshes
    env.mgr.publish(Change(p.id, State.ACTIVE))
    assert env.refreshes == before + 1


# ---- profiles window -------------------------------------------------------------------------
def test_new_profile_has_unique_name_and_default_folder(env: Env) -> None:
    env.add("New server")
    p = env.ctl.new_profile()
    assert p.name == "New server 2" and Path(p.mount_point).parts[-2:] == ("ValhallISC", "New server 2")
    assert not env.ctl.is_saved(p.id)


def test_save_new_and_update(env: Env) -> None:
    p = env.ctl.new_profile()
    assert env.ctl.save_profile(p, "secret") == {}
    assert env.store.password(p.id) == "secret"
    p.port = 1972
    assert env.ctl.save_profile(p, None) == {}
    assert env.store.get(p.id).port == 1972 and env.store.password(p.id) == "secret"


def test_save_invalid_returns_field_errors(env: Env) -> None:
    p = env.ctl.new_profile()
    p.host = ""
    p.port = 0
    assert set(env.ctl.save_profile(p, "x")) == {"host", "port"}
    assert not env.ctl.is_saved(p.id)


def test_save_active_refused(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    assert "name" in env.ctl.save_profile(p, None)


def test_delete_confirm_and_active_refused(env: Env) -> None:
    p = env.add()
    env.mgr.states[p.id] = State.ACTIVE
    assert env.ctl.delete_profile(p.id) is False and env.prompter.kinds() == ["error"]
    env.mgr.states[p.id] = State.INACTIVE
    env.prompter.answers = [False]
    assert env.ctl.delete_profile(p.id) is False and env.ctl.is_saved(p.id)
    env.prompter.answers = [True]
    assert env.ctl.delete_profile(p.id) is True and not env.ctl.is_saved(p.id)
    assert env.store.password(p.id) is None


def test_test_connection_unreachable(env: Env) -> None:
    p = Profile(name="x", host="127.0.0.1", port=9, username="u", mount_point="/m")
    result = env.ctl.test_connection(p, "pw")
    assert not result.ok and "connect" in result.message.lower()
    assert "try port 80" in result.hint  # design review 2 #9: IRIS 2023.2+ has no private web server


@pytest.mark.parametrize(
    ("port", "https", "expected"),
    [(52773, False, "try port 80 "), (52773, True, "try port 443 "), (80, False, "URL prefix")],
)
def test_port_hint(port: int, https: bool, expected: str) -> None:
    from irisfs.gui.controller import port_hint

    p = Profile(name="x", host="h", port=port, username="u", mount_point="/m", https=https)
    assert expected in port_hint(p)


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_prepare_folder_per_platform(env: Env, system: str) -> None:
    # Regression (first Windows run): the default ~/ValhallISC/<name> parent was never created on Windows.
    env.ctl.system = system
    target = env.tmp / "ValhallISC" / "Local"
    env.ctl._prepare_folder(str(target))
    assert target.parent.is_dir()
    assert target.exists() is (system != "Windows")  # WinFsp needs the folder itself to be missing


def test_prepare_folder_drive_letter_is_noop(env: Env) -> None:
    env.ctl.system = "Windows"
    env.ctl._prepare_folder("X:")


# ---- design review 1: row subtitles, summary, open folder --------------------------------------
@pytest.mark.parametrize(
    ("state", "external", "read_only", "expected"),
    [
        (State.INACTIVE, False, False, "iris:52773 · not mounted"),
        (State.MOUNTING, False, False, "Mounting…"),
        (State.UNMOUNTING, False, False, "Unmounting…"),
        (State.ACTIVE, False, True, "Mounted · /m · read-only"),
        (State.ACTIVE, True, False, "Mounted from CLI · /m"),
    ],
)
def test_item_subtitle(state: State, external: bool, read_only: bool, expected: str) -> None:
    item = ProfileItem("id", "X", state, "/m", external, "iris", 52773, read_only)
    assert item.subtitle == expected


@pytest.mark.parametrize(
    ("state", "read_only", "expected"),
    [
        (State.INACTIVE, False, "iris:52773 · not mounted"),
        (State.MOUNTING, False, "Mounting…"),
        (State.ACTIVE, True, "Mounted · read-only"),
        (State.ACTIVE, False, "Mounted"),
    ],
)
def test_row_subtitle_is_one_short_line(state: State, read_only: bool, expected: str) -> None:
    # design review 2 #3: no path in the list row (it wrapped); the banner shows it
    item = ProfileItem("id", "X", state, "/very/long/mount/point", False, "iris", 52773, read_only)
    assert item.row_subtitle == expected


def test_home_is_shortened() -> None:
    from pathlib import Path as P

    from irisfs.gui.controller import short_path

    assert short_path(str(P.home() / "ValhallISC" / "Dev")).startswith("~")
    assert short_path("/mnt/x") == "/mnt/x"


def test_summary(env: Env) -> None:
    assert env.ctl.summary() == "no servers yet"
    a = env.add("A")
    env.add("B")
    env.mgr.states[a.id] = State.ACTIVE
    assert env.ctl.summary() == "1 of 2 mounted"


def test_open_folder(env: Env) -> None:
    p = env.add()
    opened: list[str] = []
    env.ctl._opener = opened.append
    env.ctl.open_folder(p.id)
    assert opened == [p.mount_point]

    def fail(path: str) -> None:
        raise OSError("no file manager")

    env.ctl._opener = fail
    env.ctl.open_folder(p.id)
    assert env.prompter.kinds()[-1] == "error"


def test_missing_fuse_opens_the_install_help(env: Env) -> None:
    from irisfs.mount import fuse_help

    p = env.add()
    shown: list[fuse_help.Advice] = []
    env.ctl.fuse_missing_handler = shown.append
    env.ctl.fuse_check = lambda: fuse_help.Advice(
        "FUSE 3 is needed", "install it", ("sudo apt install fuse3",)
    )
    env.ctl.on_profile_clicked(p.id)
    assert env.mgr.calls == [] and shown and shown[0].commands == ("sudo apt install fuse3",)
    assert env.prompter.log == []  # the dialog replaces the generic error


def test_mount_mounts_only_an_inactive_profile(env: Env) -> None:
    p = env.add("Box")
    env.ctl.mount(p.id)
    assert ("mount", p.id) in env.mgr.calls
    env.mgr.calls.clear()
    env.ctl.mount(p.id)  # already mounting: nothing more
    assert not any(c[0] == "mount" for c in env.mgr.calls)
