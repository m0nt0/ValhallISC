"""Smoke tests of the real wx widgets, driven programmatically (layout after design review 1)."""

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


def click(button: Any) -> None:
    import wx

    button.ProcessEvent(wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId()))


def test_profile_list_has_one_border_style(frame: Any) -> None:
    # Regression: HLB_DEFAULT_STYLE | BORDER_THEME set two border styles; wxMSW asserts ("unknown border
    # style") and the Profiles window never opened on Windows.
    import wx

    border = frame.list.GetWindowStyleFlag() & wx.BORDER_MASK
    assert border == wx.BORDER_THEME


def test_windows_carry_the_logo(frame: Any) -> None:
    # Windows taskbar and Linux window list show the window's icons, not the executable's (ADR-016)
    icons = frame.GetIcons()
    assert icons.GetIconCount() == 7
    assert icons.GetIcon(48).IsOk() and icons.GetIcon(48).GetWidth() == 48


def test_empty_store_shows_empty_form(frame: Any) -> None:
    assert frame.row_count() == 0
    assert frame.current is None
    assert not frame.name.IsEnabled() and not frame.btn_save.IsEnabled() and not frame.btn_delete.IsEnabled()


def test_add_fill_save(frame: Any, env: Env, wx_app: Any) -> None:
    from irisfs.gui.profiles_frame import UNCHANGED

    frame.on_add()
    pump(wx_app)
    assert frame.current_is_new and frame.row_count() == 1
    assert "not saved yet" in frame.list.GetString(0)
    assert frame.name.GetValue() == "New server" and frame.btn_save.IsEnabled()
    frame.name.SetValue("Dev IRIS")
    frame.host.SetValue("10.0.0.5")
    frame.port.SetValue("1972")
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
    assert frame.password.GetHint() == UNCHANGED


def test_connect_saves_then_mounts(frame: Any, env: Env, wx_app: Any) -> None:
    frame.on_add()
    frame.name.SetValue("Dev IRIS")
    frame.host.SetValue("10.0.0.5")
    frame.user.SetValue("dev")
    frame.password.SetValue("s3cret")
    frame.mount_point.SetPath(str(env.tmp / "devmnt"))
    assert frame.btn_connect.IsEnabled()
    click(frame.btn_connect)
    pump(wx_app)
    [saved] = env.store.profiles()  # saved first: a mount needs the stored profile and password
    assert ("mount", saved.id) in env.mgr.calls
    frame.reload_list(saved.id)
    pump(wx_app)
    assert not frame.btn_connect.IsEnabled()  # mounting: the banner offers the actions now


def test_connect_with_invalid_form_mounts_nothing(frame: Any, env: Env) -> None:
    frame.on_add()
    frame.host.SetValue("http://bad host")
    click(frame.btn_connect)
    assert frame.field_errors["host"].IsShown()
    assert not any(c[0] == "mount" for c in env.mgr.calls)


def test_invalid_save_shows_errors_under_fields(frame: Any, env: Env) -> None:
    frame.on_add()
    frame.host.SetValue("http://bad host")
    assert frame.on_save() is False
    host_error = frame.field_errors["host"]
    assert host_error.IsShown() and "host name" in host_error.GetLabel()
    assert not frame.field_errors["port"].IsShown()
    assert env.store.profiles() == []


def test_port_must_be_a_number(frame: Any) -> None:
    frame.on_add()
    frame.port.SetValue("abc")
    assert frame.on_save() is False
    assert frame.field_errors["port"].IsShown()


def test_mounted_profile_is_read_only_with_banner(frame: Any, env: Env, wx_app: Any) -> None:
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
    assert not frame.btn_delete.IsEnabled()
    # design review #5: the banner offers the actions instead of a dead end
    assert frame.banner.IsShown() and frame.btn_open.IsShown() and frame.btn_unmount.IsShown()
    assert "Mounted" in frame.banner_text.GetLabel()


def test_banner_actions(frame: Any, env: Env, wx_app: Any) -> None:
    p = env.add("Mounted one")
    env.mgr.states[p.id] = State.ACTIVE
    opened: list[str] = []
    env.ctl._opener = opened.append
    frame.reload_list(p.id)
    pump(wx_app)
    click(frame.btn_open)
    assert opened == [p.mount_point]
    env.prompter.answers = [True]
    frame.on_unmount()
    assert env.mgr.calls[-1] == ("unmount", (p.id, False))


def test_no_banner_for_idle_profile(frame: Any, env: Env, wx_app: Any) -> None:
    p = env.add("Idle")
    frame.reload_list(p.id)
    pump(wx_app)
    assert not frame.banner.IsShown() and frame.name.IsEnabled()


def test_delete_inactive_with_confirmation(frame: Any, env: Env, wx_app: Any) -> None:
    p = env.add("Old")
    frame.reload_list(p.id)
    pump(wx_app)
    env.prompter.answers = [True]
    frame.on_delete()
    pump(wx_app)
    assert env.store.profiles() == [] and frame.row_count() == 0


def test_switching_with_unsaved_changes_asks(frame: Any, env: Env, wx_app: Any) -> None:
    a, b = env.add("A"), env.add("B")
    frame.reload_list(a.id)
    pump(wx_app)
    frame.host.SetValue("changed")
    assert frame.dirty
    env.prompter.answers = [False]  # keep editing
    frame.select_row(1)
    pump(wx_app)
    assert frame.current.id == a.id and frame.host.GetValue() == "changed"
    env.prompter.answers = [True]  # discard
    frame.select_row(1)
    pump(wx_app)
    assert frame.current.id == b.id


def test_list_rows_show_state(frame: Any, env: Env, wx_app: Any) -> None:
    a = env.add("Alpha")
    env.mgr.states[a.id] = State.ACTIVE
    env.add("Beta")
    frame.reload_list(None)
    pump(wx_app)
    rows = [frame.list.GetString(i) for i in range(frame.row_count())]
    assert "Mounted ·" in rows[0] and "Alpha" in rows[0]
    assert "localhost:52773 · not mounted" in rows[1]


def test_options_tab_error_switches_tab(frame: Any) -> None:
    frame.on_add()
    frame.prefix.SetValue("iris")  # invalid: must start with /
    assert frame.on_save() is False
    assert frame.tabs.GetSelection() == 1 and frame.field_errors["path_prefix"].IsShown()


def test_password_show_toggle(frame: Any) -> None:
    frame.on_add()
    frame.password.SetValue("secret")
    frame.btn_eye.SetValue(True)
    frame._toggle_password()
    assert frame.password_plain.IsShown() and frame.password_plain.GetValue() == "secret"
    assert frame._form_password() == "secret"


def test_fields_scroll_above_fixed_footer(frame: Any, wx_app: Any) -> None:
    # design review #1: content scrolls in its own area; the footer never covers it
    frame.on_add()
    frame.Layout()
    pump(wx_app)  # (no SetSize here: GTK resizes asynchronously and would report half-updated rects)
    assert frame.fields_panel.GetScreenRect().bottom <= frame.btn_save.GetScreenRect().top


def test_list_buttons_use_trash_and_plus_glyphs(frame: Any) -> None:
    for button in (frame.btn_add, frame.btn_delete):
        assert button.GetBitmap().IsOk() and button.GetToolTipText()


def test_menus(env: Env, wx_app: Any) -> None:
    from irisfs.gui.tray import build_menu

    a, b = env.add("A"), env.add("B")
    c = env.add("C")
    env.mgr.states[a.id] = State.ACTIVE
    env.mgr.states[b.id] = State.MOUNTING
    menu, actions = build_menu(env.ctl.profile_items(), "combined", env.ctl.summary())
    items = [i for i in menu.GetMenuItems() if not i.IsSeparator()]
    labels = [i.GetItemLabelText() for i in items]
    assert labels == [
        "ValhallISC — 1 of 3 mounted",
        "A — Mounted",
        "B — Connecting…",
        "C",
        "Profiles…",
        "Quit ValhallISC",
    ]
    assert not items[0].IsEnabled()  # header
    sub = items[1].GetSubMenu()  # mounted: a submenu (native menus cannot hold buttons)
    assert sub is not None
    sub_labels = [i.GetItemLabelText() for i in sub.GetMenuItems() if not i.IsSeparator()]
    assert "Mounted ·" in sub_labels[0] and sub_labels[1:] == ["Open folder", "Unmount…"]
    assert not items[2].IsEnabled()  # connecting
    assert sorted(actions.values()) == sorted([("open", a.id), ("unmount", a.id), ("toggle", c.id)])
    empty, actions = build_menu([], "profiles", "no servers yet")
    assert not [i for i in empty.GetMenuItems() if not i.IsSeparator()][-1].IsEnabled() and actions == {}


def test_icons_load(wx_app: Any) -> None:
    from irisfs.gui import icons

    active, inactive = icons.tray_bundle(active=True), icons.tray_bundle(active=False)
    assert active.IsOk() and inactive.IsOk()
    assert active.GetDefaultSize().width == 22


def test_prompter_ignores_destroyed_parent(env: Env, wx_app: Any) -> None:
    # Regression (Windows): an error after the Profiles window was closed crashed with
    # "wrapped C/C++ object of type ProfilesFrame has been deleted".
    from irisfs.gui.profiles_frame import ProfilesFrame
    from irisfs.gui.wxprompter import WxPrompter

    f = ProfilesFrame(env.ctl)
    f.Destroy()
    pump(wx_app)
    assert WxPrompter(parent_getter=lambda: f)._parent() is None


def test_app_starts_and_exits(tmp_path: Path, wx_app: Any) -> None:
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / "src"),
        VALHALLISC_CONFIG_DIR=str(tmp_path),
        VALHALLISC_EXIT_AFTER="2",
        VALHALLISC_SECRETS="file",
    )
    r = subprocess.run(
        [sys.executable, "-m", "irisfs", "gui"], env=env, capture_output=True, text=True, timeout=60
    )
    assert r.returncode == 0, r.stderr[-2000:]
    assert "started" in r.stderr and "exiting" in r.stderr


def test_fuse_missing_dialog(wx_app: Any) -> None:
    from irisfs.gui.fuse_dialog import FuseMissingDialog
    from irisfs.mount import fuse_help

    advice = fuse_help.Advice(
        "FUSE 3 is needed",
        "Install it with:",
        ("sudo apt install fuse3",),
        (("libfuse project", fuse_help.LIBFUSE_URL),),
        ("allow it in Privacy & Security",),
    )
    results: list[fuse_help.Advice | None] = [advice, None]
    dlg = FuseMissingDialog(advice, lambda: results.pop(0))
    try:
        assert dlg.commands is not None and dlg.commands.GetValue() == "sudo apt install fuse3"
        assert [link.GetURL() for link in dlg.links] == [fuse_help.LIBFUSE_URL]
        import wx

        notes = [w.GetLabelText() for w in dlg.GetChildren()[0].GetChildren() if isinstance(w, wx.StaticText)]
        assert any("Privacy & Security" in n for n in notes)  # '&' must survive (wx mnemonic escaping)
        assert dlg.on_check() is False and "Still not found" in dlg.status.GetLabel()
        assert dlg.on_check() is True and "FUSE found" in dlg.status.GetLabel()
    finally:
        dlg.Destroy()
        pump(wx_app)
