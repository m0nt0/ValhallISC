"""Render the ValhallISC windows to PNG (for design reviews), plus menus.txt with the menus' structure.

macOS:  python scripts/screenshots.py <dir> [light|dark]     (AppKit draws the window itself)
Linux:  xvfb-run -a -s "-screen 0 1400x1000x24" python scripts/screenshots.py <dir>   (ImageMagick)"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("VALHALLISC_SECRETS", "file")
os.environ["VALHALLISC_CONFIG_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wx
from tests.unit.test_controller import FakeManager, FakePrompter

from irisfs.config.profile import Profile
from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import ProfileStore
from irisfs.gui.controller import AppController
from irisfs.gui.profiles_frame import ProfilesFrame
from irisfs.mount.manager import State


def settle() -> None:
    for _ in range(20):
        wx.SafeYield()
        wx.MilliSleep(25)


def grab(window: wx.Window, path: Path) -> None:
    window.Refresh()
    window.Update()
    settle()
    if sys.platform == "darwin":
        _grab_macos(window, path)
        return
    rect = window.GetScreenRect()
    # wx.ScreenDC returns stale pixels under Xvfb; ImageMagick's `import` reads the real X framebuffer.
    import subprocess

    geometry = f"{rect.width}x{rect.height}+{rect.x}+{rect.y}"
    subprocess.run(["import", "-window", "root", "-crop", geometry, "+repage", str(path)], check=True)
    print("wrote", path)


def _grab_macos(window: wx.Window, path: Path) -> None:
    """The window draws itself into a bitmap (no screen-recording permission needed), title bar included."""
    from AppKit import NSApp, NSBitmapImageFileTypePNG

    title = window.GetTitle()
    ns_window = next(w for w in NSApp.windows() if w.isVisible() and w.title() == title)
    view = ns_window.contentView().superview()  # the frame view: title bar + content
    rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(
        str(path), True
    )
    print("wrote", path)


def _appearance(theme: str | None) -> None:
    if sys.platform != "darwin" or theme is None:
        return
    from AppKit import NSApp, NSAppearance

    name = "NSAppearanceNameDarkAqua" if theme == "dark" else "NSAppearanceNameAqua"
    NSApp.setAppearance_(NSAppearance.appearanceNamed_(name))


def _menu_text(menu: wx.Menu, indent: str = "") -> list[str]:
    lines = []
    for item in menu.GetMenuItems():
        if item.IsSeparator():
            lines.append(f"{indent}────────")
            continue
        label = item.GetItemLabelText() + ("" if item.IsEnabled() else "   (disabled)")
        lines.append(indent + label)
        if item.GetSubMenu() is not None:
            lines += _menu_text(item.GetSubMenu(), indent + "    ")
    return lines


def main(out: Path, theme: str | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = wx.App(False)
    _appearance(theme)
    cfg = Path(os.environ["VALHALLISC_CONFIG_DIR"])
    store = ProfileStore(cfg / "profiles.json", secrets=FileSecretStore(cfg / "secrets.json"))
    mgr = FakeManager()
    store.is_active = mgr.is_active
    ctl = AppController(
        store, mgr, FakePrompter(), request_exit=lambda: None, call_ui=lambda t: t(), run_bg=lambda t: t()
    )
    home = "/home/me/ValhallISC"
    dev = store.add(
        Profile(name="Dev IRIS", host="iris.example.com", username="_SYSTEM", mount_point=f"{home}/Dev IRIS"),
        password="x",
    )
    prod = store.add(
        Profile(
            name="Production",
            host="iris.example.com",
            port=443,
            https=True,
            path_prefix="/iris",
            username="deploy",
            read_only=True,
            mount_point=f"{home}/Production",
        ),
        password="x",
    )
    store.add(
        Profile(name="Test server", host="test.local", username="tester", mount_point=f"{home}/Test"),
        password="x",
    )
    mgr.states[prod.id] = State.ACTIVE
    test = next(p for p in store.profiles() if p.name == "Test server")

    frame = ProfilesFrame(ctl)
    frame.SetPosition(wx.Point(20, 20))
    frame.Show()
    frame.reload_list(dev.id)
    print("  form enabled:", frame.name.IsEnabled(), "save enabled:", frame.btn_save.IsEnabled())
    grab(frame, out / "profiles-edit.png")

    frame.tabs.SetSelection(1)
    grab(frame, out / "profiles-options.png")
    frame.tabs.SetSelection(0)

    frame.reload_list(prod.id)
    grab(frame, out / "profiles-mounted-readonly.png")

    mgr.states[test.id] = State.MOUNTING
    frame.reload_list(test.id)
    grab(frame, out / "profiles-connecting.png")
    del mgr.states[test.id]

    frame.reload_list(dev.id)
    frame.host.SetValue("127.0.0.1")
    frame.port.SetValue("9")  # nothing listens there: refused, so the port hint shows
    frame.on_test()
    for _ in range(200):
        if frame.port_hint.IsShown():
            break
        settle()
    grab(frame, out / "profiles-port-hint.png")
    frame.on_revert()

    from irisfs.gui import tray

    menus = []
    for kind in ("combined", "profiles", "actions"):  # macOS: combined; Windows/Linux: left / right click
        menu, _actions = tray.build_menu(ctl.profile_items(), kind, ctl.summary())
        menus += [f"== tray / menu-bar icon menu ({kind})", *_menu_text(menu), ""]
    menus += [
        "== macOS menu bar (app active)",
        "ValhallISC: About ValhallISC, ..., Quit ValhallISC  Cmd-Q",
        "File: Profiles…  Cmd-,",
        "Window: (standard)",
    ]
    (out / "menus.txt").write_text("\n".join(menus) + "\n", encoding="utf-8")
    print("wrote", out / "menus.txt")

    frame.on_add()
    grab(frame, out / "profiles-new.png")

    frame.on_add()
    frame.host.SetValue("http://bad host")
    frame.port.SetValue("5277x")
    frame.on_save()
    grab(frame, out / "profiles-validation-errors.png")
    frame.dirty = False
    frame.Destroy()

    from irisfs.gui.fuse_dialog import FuseMissingDialog
    from irisfs.mount import fuse_help

    advice = fuse_help.advice(
        "Linux", os_release={"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS"}, library=None
    )
    assert advice is not None
    dialog = FuseMissingDialog(advice, lambda: advice)
    dialog.SetPosition(wx.Point(20, 20))
    dialog.Show()
    grab(dialog, out / "fuse-missing-linux.png")
    mac = fuse_help.advice(
        "Darwin", which=lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None, library=None
    )
    assert mac is not None
    dialog.Destroy()
    dialog = FuseMissingDialog(mac, lambda: mac)
    dialog.SetPosition(wx.Point(20, 20))
    dialog.Show()
    grab(dialog, out / "fuse-missing-macos.png")
    dialog.Destroy()
    app.Destroy()


if __name__ == "__main__":
    main(
        Path(sys.argv[1] if len(sys.argv) > 1 else "doc/screenshots"),
        sys.argv[2] if len(sys.argv) > 2 else None,
    )
