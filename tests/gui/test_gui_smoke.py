"""Smoke tests of the real wx widgets, driven programmatically."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from irisfs.mount.manager import State
from tests.conftest import ROOT
from tests.gui.conftest import pump
from tests.unit.test_controller import Env

pytestmark = pytest.mark.gui


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wx_app: Any) -> Env:
    return Env(tmp_path, monkeypatch)


@pytest.fixture
def frame(env: Env, wx_app: Any) -> Any:
    from irisfs.gui.profiles_frame import ProfilesFrame

    f = ProfilesFrame(env.ctl)
    f.Show()
    pump(wx_app)
    yield f
    if f:
        f.dirty = False
        f.Close(force=True)
        pump(wx_app)


def test_empty_store_shows_empty_form(frame: Any) -> None:
    assert frame.list.GetItemCount() == 0
    assert frame.current is None
    assert not frame.name.IsEnabled() and not frame.btn_save.IsEnabled()


def test_add_fill_save(frame: Any, env: Env, wx_app: Any) -> None:
    frame.on_add()
    pump(wx_app)
    assert frame.current_is_new and frame.list.GetItemCount() == 1
    assert frame.name.GetValue() == "New server" and frame.btn_save.IsEnabled()
    frame.name.SetValue("Dev IRIS")
    frame.host.SetValue("10.0.0.5")
    frame.port.SetValue(1972)
    frame.user.SetValue("dev")
    frame.password.SetValue("s3cret")
    frame.read_only.SetValue(True)
    frame.mount_point.SetPath(str(env.tmp / "devmnt"))
    assert frame.on_save() is True
    pump(wx_app)
    [saved] = env.store.profiles()
    assert (saved.name, saved.host, saved.port, saved.username, saved.read_only) == (
        "Dev IRIS",
        "10.0.0.5",
        1972,
        "dev",
        True,
    )
    assert env.store.password(saved.id) == "s3cret"
    assert not frame.current_is_new and not frame.dirty
    assert frame.password.GetHint() == "(unchanged)"


def test_invalid_save_shows_errors(frame: Any, env: Env, wx_app: Any) -> None:
    frame.on_add()
    frame.host.SetValue("http://bad host")
    assert frame.on_save() is False
    assert "host" in frame.errors.GetLabel().lower() or "Enter a host" in frame.errors.GetLabel()
    assert env.store.profiles() == []


def test_active_profile_is_read_only(frame: Any, env: Env, wx_app: Any) -> None:
    p = env.add("Mounted one")
    env.mgr.states[p.id] = State.ACTIVE
    frame.reload_list(p.id)
    pump(wx_app)
    assert frame.current is not None and frame.current.id == p.id
    assert frame.is_read_only()
    for ctrl in (
        frame.name,
        frame.host,
        frame.port,
        frame.user,
        frame.password,
        frame.mount_point,
        frame.btn_save,
    ):
        assert not ctrl.IsEnabled()
    assert not frame.GetToolBar().GetToolEnabled(frame.tool_delete.GetId())
    assert "unmount" in frame.note.GetLabel().lower()


def test_delete_inactive_with_confirmation(frame: Any, env: Env, wx_app: Any) -> None:
    p = env.add("Old")
    frame.reload_list(p.id)
    pump(wx_app)
    env.prompter.answers = [True]
    frame.on_delete()
    pump(wx_app)
    assert env.store.profiles() == [] and frame.list.GetItemCount() == 0


def test_switching_with_unsaved_changes_asks(frame: Any, env: Env, wx_app: Any) -> None:
    a, b = env.add("A"), env.add("B")
    frame.reload_list(a.id)
    pump(wx_app)
    frame.host.SetValue("changed")
    assert frame.dirty
    env.prompter.answers = [False]  # keep editing
    frame.list.Select(1)
    pump(wx_app)
    assert frame.current.id == a.id and frame.host.GetValue() == "changed"
    env.prompter.answers = [True]  # discard
    frame.list.Select(1)
    pump(wx_app)
    assert frame.current.id == b.id


def test_menus(env: Env, wx_app: Any) -> None:
    from irisfs.gui.tray import build_menu

    a, b = env.add("A"), env.add("B")
    env.mgr.states[a.id] = State.ACTIVE
    env.mgr.states[b.id] = State.MOUNTING
    menu, ids = build_menu(env.ctl.profile_items(), "combined")
    labels = [
        menu.FindItemById(i.GetId()).GetItemLabelText() for i in menu.GetMenuItems() if not i.IsSeparator()
    ]
    assert labels == ["A", "B (connecting…)", "Profiles…", "Quit ValhallISC"]
    items = menu.GetMenuItems()
    assert items[0].IsChecked() and not items[1].IsChecked() and not items[1].IsEnabled()
    assert set(ids.values()) == {a.id, b.id}
    left, _ = build_menu(env.ctl.profile_items(), "profiles")
    right, _ = build_menu(env.ctl.profile_items(), "actions")
    assert left.GetMenuItemCount() == 2 and right.GetMenuItemCount() == 2
    empty, ids = build_menu([], "profiles")
    assert not empty.GetMenuItems()[0].IsEnabled() and ids == {}


def test_icons_load(wx_app: Any) -> None:
    from irisfs.gui import icons

    active, inactive = icons.tray_bundle(active=True), icons.tray_bundle(active=False)
    assert active.IsOk() and inactive.IsOk()
    assert active.GetDefaultSize().width == 22


def test_app_starts_and_exits(tmp_path: Path, wx_app: Any) -> None:
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        VALHALLISC_CONFIG_DIR=str(tmp_path),
        VALHALLISC_EXIT_AFTER="2",
    )
    r = subprocess.run(
        [sys.executable, "-m", "irisfs", "gui"], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert "started" in r.stderr and "exiting" in r.stderr
