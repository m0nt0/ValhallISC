"""ValhallISC tray application entry point."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading

import wx

from irisfs import APP_NAME
from irisfs import log as logsetup
from irisfs.config import secrets
from irisfs.config.store import ProfileStore, ProfileStoreError, default_config_dir
from irisfs.gui.controller import AppController
from irisfs.gui.profiles_frame import ProfilesFrame
from irisfs.gui.tray import TrayIcon
from irisfs.gui.wxprompter import WxPrompter, bring_to_front
from irisfs.mount import fuse_help
from irisfs.mount.manager import MountManager

log = logging.getLogger(__name__)


def _hide_dock_icon() -> None:
    """Menu-bar-only app when run from source (the .app bundle sets LSUIElement instead)."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory

        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except ImportError:
        log.warning("PyObjC not available: a Dock icon will be shown")


class ValhallApp:
    def __init__(self) -> None:
        self.wx_app = wx.App(False)
        self.wx_app.SetAppName(APP_NAME)
        self.wx_app.SetAppDisplayName(APP_NAME)
        self.hidden = wx.Frame(None)  # never shown: keeps the main loop alive for a tray-only app (ADR-005)
        self.profiles: ProfilesFrame | None = None
        self.fuse_dialog: wx.Dialog | None = None
        self.tray: TrayIcon | None = None

    def start(self) -> bool:
        config_dir = default_config_dir()
        # One instance per settings folder: instances with separate settings (and mount registries) don't
        # conflict - e.g. the test suite (VALHALLISC_CONFIG_DIR) next to a running app.
        folder_id = hashlib.sha1(str(config_dir.resolve()).encode()).hexdigest()[:10]
        self.checker = wx.SingleInstanceChecker(f"{APP_NAME}-{wx.GetUserId()}-{folder_id}")
        if self.checker.IsAnotherRunning():
            log.info("another instance is already running for %s", config_dir)
            wx.MessageBox(f"{APP_NAME} is already running (see the menu bar / system tray).", APP_NAME)
            return False
        _hide_dock_icon()
        try:
            store = ProfileStore(config_dir / "profiles.json", secrets=secrets.default_store(config_dir))
        except ProfileStoreError as e:
            wx.MessageBox(str(e), APP_NAME, wx.OK | wx.ICON_ERROR)
            return False
        self.manager = MountManager(store)
        prompter = WxPrompter(parent_getter=lambda: self.profiles)
        self.controller = AppController(
            store, self.manager, prompter, request_exit=self.exit, call_ui=wx.CallAfter
        )
        if store.load_warning:
            wx.CallAfter(prompter.error, APP_NAME, store.load_warning)
        threading.Thread(target=self._cleanup, name="stale-cleanup", daemon=True).start()
        self.tray = TrayIcon(self.controller, open_profiles=self.open_profiles)
        self.controller.fuse_missing_handler = self.show_fuse_help
        wx.CallAfter(self.check_fuse)  # startup check: explain how to install FUSE if it is missing
        if not store.profiles():
            wx.CallAfter(self.open_profiles)  # first run: show where to start
        exit_after = os.environ.get("VALHALLISC_EXIT_AFTER")  # test hook: quit automatically
        if exit_after:
            wx.CallLater(int(float(exit_after) * 1000), self.exit)
        log.info("%s started", APP_NAME)
        return True

    def check_fuse(self) -> None:
        advice = self.controller.fuse_check()
        if advice is not None:
            log.warning("FUSE not usable: %s", advice.title)
            self.show_fuse_help(advice)

    def show_fuse_help(self, advice: fuse_help.Advice) -> None:
        from irisfs.gui.fuse_dialog import FuseMissingDialog

        if self.fuse_dialog:  # already open: bring it forward
            self.fuse_dialog.Raise()
            return
        bring_to_front()
        self.fuse_dialog = FuseMissingDialog(advice, self.controller.fuse_check)
        self.fuse_dialog.Show()  # modeless: the app stays usable (e.g. to edit profiles)
        self.fuse_dialog.Raise()

    def _cleanup(self) -> None:
        cleaned = self.manager.cleanup_stale()
        if cleaned:
            log.info("removed stale mounts: %s", cleaned)

    def open_profiles(self) -> None:
        if self.profiles is None or not self.profiles:
            self.profiles = ProfilesFrame(self.controller)
            self.profiles.Bind(wx.EVT_WINDOW_DESTROY, self._on_profiles_destroyed)
        bring_to_front()
        self.profiles.Show()
        self.profiles.Raise()

    def _on_profiles_destroyed(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self.profiles:
            self.profiles = None
        event.Skip()

    def exit(self) -> None:
        log.info("exiting")
        still = self.manager.unmount_all(force=True, wait=10)  # normally already empty (Quit unmounts first)
        if still:
            log.warning("still mounted at exit: %s", still)
        if self.profiles:
            self.profiles.Destroy()
        if self.tray is not None:
            self.tray.RemoveIcon()
            self.tray.Destroy()
        self.hidden.Destroy()
        self.wx_app.ExitMainLoop()

    def run(self) -> int:
        if not self.start():
            return 1
        self.wx_app.MainLoop()
        return 0


def main() -> int:
    logsetup.setup("gui", to_stderr=True)
    return ValhallApp().run()
