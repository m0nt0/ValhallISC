"""Render the ValhallISC windows to PNG (for design reviews). Run under a display, e.g. in the Linux
test container:  xvfb-run -a -s "-screen 0 1400x1000x24" python scripts/screenshots.py doc/screenshots"""

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
    rect = window.GetScreenRect()
    # wx.ScreenDC returns stale pixels under Xvfb; ImageMagick's `import` reads the real X framebuffer.
    import subprocess

    geometry = f"{rect.width}x{rect.height}+{rect.x}+{rect.y}"
    subprocess.run(["import", "-window", "root", "-crop", geometry, "+repage", str(path)], check=True)
    print("wrote", path)


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    app = wx.App(False)
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
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "doc/screenshots"))
