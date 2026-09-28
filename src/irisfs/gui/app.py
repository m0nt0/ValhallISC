"""ValhallISC tray application entry point."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
from collections.abc import Callable

import wx

from irisfs import APP_NAME
from irisfs import log as logsetup
from irisfs.config import secrets
from irisfs.config.store import ProfileStore, ProfileStoreError, default_config_dir
from irisfs.gui import instance
from irisfs.gui.controller import AppController
from irisfs.gui.profiles_frame import ProfilesFrame
from irisfs.gui.tray import TrayIcon
from irisfs.gui.wxprompter import WxPrompter, bring_to_front
from irisfs.mount import fuse_help
from irisfs.mount.manager import MountManager

log = logging.getLogger(__name__)


class _WxApp(wx.App):  # type: ignore[misc]
    """wx.App that forwards macOS "reopen" (Dock icon clicked, app opened again from Finder or Launchpad)."""

    on_reopen: Callable[[], None] | None = None

    def MacReopenApp(self) -> None:
        if self.on_reopen is not None:
            self.on_reopen()


class ValhallApp:
    def __init__(self) -> None:
        self.wx_app = _WxApp(False)
        self.wx_app.SetAppName(APP_NAME)
        self.wx_app.SetAppDisplayName(APP_NAME)
        self.hidden = wx.Frame(None)  # never shown: keeps the main loop alive for a tray-only app (ADR-005)
        self.profiles: ProfilesFrame | None = None
        self.fuse_dialog: wx.Dialog | None = None
        self.tray: TrayIcon | None = None
        self.handed_over = False  # another instance was running and got the request instead

    def start(self) -> bool:
        config_dir = default_config_dir()
        # One instance per settings folder: instances with separate settings (and mount registries) don't
        # conflict - e.g. the test suite (VALHALLISC_CONFIG_DIR) next to a running app.
        folder_id = hashlib.sha1(str(config_dir.resolve()).encode()).hexdigest()[:10]
        self.checker = wx.SingleInstanceChecker(f"{APP_NAME}-{wx.GetUserId()}-{folder_id}")
        if self.checker.IsAnotherRunning():
            # Launched again: the running instance shows its window (ADR-016); this process just leaves.
            log.info("already running for %s: asking it to show the Profiles window", config_dir)
            instance.request_show(config_dir)
            self.handed_over = True
            return False
        self.config_dir = config_dir
        instance.take_show_request(config_dir)  # left over from an earlier run: ignore
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
        self.wx_app.on_reopen = self._on_reopen  # macOS: Dock icon, or the app opened again
        if sys.platform == "darwin":
            # Cmd-Q and Quit in the Dock arrive as a request to end the session: same confirmation (and
            # unmount) as the menu's Quit. Vetoed here; the controller exits once the servers are unmounted.
            # (Elsewhere the event only comes with a system shutdown, which must not wait for a dialog.)
            self.wx_app.Bind(wx.EVT_QUERY_END_SESSION, self._on_quit_request)
        self.show_poll = wx.Timer(self.hidden)
        self.hidden.Bind(wx.EVT_TIMER, self._poll_show_request, self.show_poll)
        self.show_poll.Start(1000)
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

    def _poll_show_request(self, _event: wx.TimerEvent) -> None:
        if instance.take_show_request(self.config_dir):
            log.info("launched again: showing the Profiles window")
            self.open_profiles()

    def _on_reopen(self) -> None:
        log.info("reopened (Dock or Finder): showing the Profiles window")
        self.open_profiles()

    def _on_quit_request(self, event: wx.CloseEvent) -> None:
        log.info("quit requested (Cmd-Q, Dock or system)")
        if event.CanVeto():
            event.Veto()
            wx.CallAfter(self.controller.on_quit)
        else:
            event.Skip()

    def _on_profiles_destroyed(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self.profiles:
            self.profiles = None
        event.Skip()

    def exit(self) -> None:
        log.info("exiting")
        if getattr(self, "show_poll", None) is not None:
            self.show_poll.Stop()
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
            return 0 if self.handed_over else 1
        self.wx_app.MainLoop()
        return 0


def main() -> int:
    logsetup.setup("gui", to_stderr=True)
    return ValhallApp().run()
