"""Prompter implementation with wx dialogs and system notifications."""

from __future__ import annotations

import logging
import sys

import wx
import wx.adv

log = logging.getLogger(__name__)


def bring_to_front() -> None:
    """A tray-only (accessory) app must activate itself, or its dialogs open behind other windows."""
    if sys.platform == "darwin":
        try:
            from AppKit import NSApp

            NSApp.activateIgnoringOtherApps_(True)
        except ImportError:
            pass


class WxPrompter:
    def __init__(self, parent_getter: object = None) -> None:
        self._parent_getter = parent_getter

    def _parent(self) -> wx.Window | None:
        if callable(self._parent_getter):
            window = self._parent_getter()
            # A closed Profiles window may still be referenced; its wrapper is then falsy (C++ side deleted).
            if isinstance(window, wx.Window) and bool(window) and window.IsShown():
                return window
        return None

    def confirm(self, title: str, message: str, *, yes: str = "Yes", no: str = "No") -> bool:
        bring_to_front()
        dlg = wx.MessageDialog(self._parent(), message, title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)
        dlg.SetYesNoLabels(yes, no)
        try:
            return bool(dlg.ShowModal() == wx.ID_YES)
        finally:
            dlg.Destroy()

    def _message(self, title: str, message: str, style: int) -> None:
        bring_to_front()
        dlg = wx.MessageDialog(self._parent(), message, title, wx.OK | style)
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def error(self, title: str, message: str) -> None:
        log.warning("%s: %s", title, message)
        self._message(title, message, wx.ICON_ERROR)

    def info(self, title: str, message: str) -> None:
        self._message(title, message, wx.ICON_INFORMATION)

    def notify(self, title: str, message: str) -> None:
        log.info("notification: %s: %s", title, message)
        try:
            note = wx.adv.NotificationMessage(title, message)
            note.SetTitle(title)
            note.Show(timeout=wx.adv.NotificationMessage.Timeout_Auto)
        except Exception:  # notifications are best effort (e.g. no notification daemon)
            log.debug("could not show notification", exc_info=True)
