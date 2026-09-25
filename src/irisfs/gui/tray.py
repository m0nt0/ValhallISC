"""Tray / menu-bar icon (plan UI-1..UI-6, UI-10)."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable

import wx
import wx.adv

from irisfs import APP_NAME
from irisfs.gui import icons
from irisfs.gui.controller import AppController, ProfileItem

log = logging.getLogger(__name__)

ID_PROFILES = wx.NewIdRef()
ID_QUIT = wx.ID_EXIT


def click_mode() -> str:
    """'combined' (one menu for any click) or 'split' (left: servers, right: Profiles…/Quit).
    macOS never delivers separate left/right tray clicks (ADR-005). VALHALLISC_TRAY_MODE overrides."""
    forced = os.environ.get("VALHALLISC_TRAY_MODE", "").lower()
    if forced in ("combined", "split"):
        return forced
    return "combined" if sys.platform == "darwin" else "split"


def append_profiles(menu: wx.Menu, items: list[ProfileItem]) -> dict[int, str]:
    """Add one check item per profile (checked = mounted). Returns {menu id: profile id}."""
    ids: dict[int, str] = {}
    if not items:
        empty = menu.Append(wx.ID_ANY, "No servers yet — open Profiles…")
        empty.Enable(False)
        return ids
    for item in items:
        mi = menu.AppendCheckItem(wx.ID_ANY, item.label)
        mi.Check(item.state.value == "active")
        mi.Enable(item.enabled)
        ids[mi.GetId()] = item.id
    return ids


def append_actions(menu: wx.Menu) -> None:
    menu.Append(ID_PROFILES, "Profiles…")
    menu.Append(ID_QUIT, f"Quit {APP_NAME}")


def build_menu(items: list[ProfileItem], kind: str) -> tuple[wx.Menu, dict[int, str]]:
    """kind: 'combined' | 'profiles' | 'actions'."""
    menu = wx.Menu()
    ids: dict[int, str] = {}
    if kind in ("combined", "profiles"):
        ids = append_profiles(menu, items)
    if kind == "combined":
        menu.AppendSeparator()
    if kind in ("combined", "actions"):
        append_actions(menu)
    return menu, ids


class TrayIcon(wx.adv.TaskBarIcon):
    def __init__(self, controller: AppController, open_profiles: Callable[[], None]) -> None:
        super().__init__()
        self.controller = controller
        self.open_profiles = open_profiles
        self.mode = click_mode()
        self._menu_ids: dict[int, str] = {}
        log.info("tray click mode: %s", self.mode)
        self.Bind(wx.EVT_MENU, self._on_menu)
        if self.mode == "split":
            self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self._on_left_click)
        controller.add_listener(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        items = self.controller.profile_items()
        mounted = [i for i in items if i.state.value == "active"]
        tooltip = f"{APP_NAME} — {len(mounted)} mounted" if mounted else f"{APP_NAME} — nothing mounted"
        # SetIcon replaces the NSImage on macOS, so the template flag must be set again every time.
        self.SetIcon(icons.tray_bundle(active=bool(mounted)), tooltip)
        wx.CallAfter(icons.mark_tray_template)

    # right click (split) or any click (combined)
    def CreatePopupMenu(self) -> wx.Menu:
        kind = "combined" if self.mode == "combined" else "actions"
        menu, self._menu_ids = build_menu(self.controller.profile_items(), kind)
        return menu

    def _on_left_click(self, _event: wx.Event) -> None:
        menu, self._menu_ids = build_menu(self.controller.profile_items(), "profiles")
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_menu(self, event: wx.CommandEvent) -> None:
        menu_id = event.GetId()
        if menu_id == ID_QUIT:
            wx.CallAfter(self.controller.on_quit)
        elif menu_id == ID_PROFILES:
            wx.CallAfter(self.open_profiles)
        elif menu_id in self._menu_ids:
            profile_id = self._menu_ids[menu_id]
            wx.CallAfter(self.controller.on_profile_clicked, profile_id)
