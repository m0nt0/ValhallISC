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


# menu id -> (action, profile id); action: "toggle" (mount, or unmount a mounted one), "open", "unmount"
MenuActions = dict[int, tuple[str, str]]


def append_profiles(menu: wx.Menu, items: list[ProfileItem], summary: str) -> MenuActions:
    """Header, then one entry per profile (design review): a mounted profile is a submenu with Open folder /
    Unmount (native menus cannot hold buttons); a changing one is disabled with its state word; an idle one
    says "Mount" and mounts when clicked (design review 2 #10). Each entry carries its state dot."""
    actions: MenuActions = {}
    header = menu.Append(wx.ID_ANY, f"{APP_NAME} — {summary}")
    header.Enable(False)
    menu.AppendSeparator()
    if not items:
        empty = menu.Append(wx.ID_ANY, "No servers yet — open Profiles…")
        empty.Enable(False)
        return actions
    for item in items:
        # the bitmap must be set before the item is appended (wxMSW)
        dot = icons.dot_bundle(icons.state_dot(item.mounted, item.active))
        if item.mounted:
            sub = wx.Menu()
            where = sub.Append(wx.ID_ANY, item.subtitle)
            where.Enable(False)
            sub.AppendSeparator()
            open_item = sub.Append(wx.ID_ANY, "Open folder")
            unmount_item = sub.Append(wx.ID_ANY, "Unmount…")
            actions[open_item.GetId()] = ("open", item.id)
            actions[unmount_item.GetId()] = ("unmount", item.id)
            entry = wx.MenuItem(menu, wx.ID_ANY, f"{item.name} — {item.status}", subMenu=sub)
            entry.SetBitmap(dot)
            menu.Append(entry)
        elif item.active:  # mounting / unmounting
            entry = wx.MenuItem(menu, wx.ID_ANY, f"{item.name} — {item.status}")
            entry.SetBitmap(dot)
            menu.Append(entry)
            entry.Enable(False)
        else:
            entry = wx.MenuItem(
                menu, wx.ID_ANY, f"{item.name} — Mount", helpString=f"Mount {item.name} at {item.mount_point}"
            )
            entry.SetBitmap(dot)
            menu.Append(entry)
            actions[entry.GetId()] = ("toggle", item.id)
    return actions


def append_actions(menu: wx.Menu) -> None:
    menu.Append(ID_PROFILES, "Profiles…")
    menu.Append(ID_QUIT, f"Quit {APP_NAME}")


def build_menu(items: list[ProfileItem], kind: str, summary: str = "") -> tuple[wx.Menu, MenuActions]:
    """kind: 'combined' | 'profiles' | 'actions'."""
    menu = wx.Menu()
    actions: MenuActions = {}
    if kind in ("combined", "profiles"):
        actions = append_profiles(menu, items, summary)
    if kind == "combined":
        menu.AppendSeparator()
    if kind in ("combined", "actions"):
        append_actions(menu)
    return menu, actions


class TrayIcon(wx.adv.TaskBarIcon):
    def __init__(self, controller: AppController, open_profiles: Callable[[], None]) -> None:
        super().__init__()
        self.controller = controller
        self.open_profiles = open_profiles
        self.mode = click_mode()
        self._menu_ids: MenuActions = {}
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
        menu, self._menu_ids = build_menu(self.controller.profile_items(), kind, self.controller.summary())
        return menu

    def _on_left_click(self, _event: wx.Event) -> None:
        menu, self._menu_ids = build_menu(
            self.controller.profile_items(), "profiles", self.controller.summary()
        )
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_menu(self, event: wx.CommandEvent) -> None:
        menu_id = event.GetId()
        if menu_id == ID_QUIT:
            wx.CallAfter(self.controller.on_quit)
        elif menu_id == ID_PROFILES:
            wx.CallAfter(self.open_profiles)
        elif menu_id in self._menu_ids:
            action, profile_id = self._menu_ids[menu_id]
            if action == "open":
                wx.CallAfter(self.controller.open_folder, profile_id)
            else:  # "toggle" mounts an idle profile; "unmount" asks for confirmation first
                wx.CallAfter(self.controller.on_profile_clicked, profile_id)
