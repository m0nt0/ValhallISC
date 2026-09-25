"""Tray icon images built from the ValhallISC logo (see scripts/make_icons.py and ADR-005)."""

from __future__ import annotations

import logging
import os
import sys
from importlib import resources

import wx

log = logging.getLogger(__name__)
INACTIVE_ALPHA = 0.45  # dimmed glyph while nothing is mounted


def _load(name: str) -> wx.Image:
    data = resources.files("irisfs.gui").joinpath("icons", name).read_bytes()
    import io

    return wx.Image(io.BytesIO(data), wx.BITMAP_TYPE_PNG)


def _variant(img: wx.Image, *, active: bool, color: tuple[int, int, int]) -> wx.Bitmap:
    img = img.Copy()
    if not img.HasAlpha():
        img.InitAlpha()
    img.SetRGB(wx.Rect(0, 0, img.GetWidth(), img.GetHeight()), *color)
    if not active:
        img = img.AdjustChannels(1.0, 1.0, 1.0, INACTIVE_ALPHA)
    return wx.Bitmap(img)


def tray_color() -> tuple[int, int, int]:
    """Glyph color for trays without template images (Linux/Windows). VALHALLISC_TRAY_COLOR=light|dark
    overrides; otherwise white on dark system themes, black on light ones."""
    forced = os.environ.get("VALHALLISC_TRAY_COLOR", "").lower()
    if forced in ("light", "dark"):
        return (0, 0, 0) if forced == "light" else (255, 255, 255)
    try:
        dark = wx.SystemSettings.GetAppearance().IsDark()
    except AttributeError:
        dark = False
    return (255, 255, 255) if dark else (0, 0, 0)


def tray_bundle(*, active: bool) -> wx.BitmapBundle:
    color = (0, 0, 0) if sys.platform == "darwin" else tray_color()
    bitmaps = [_variant(_load(n), active=active, color=color) for n in ("tray.png", "tray@2x.png")]
    return wx.BitmapBundle.FromBitmaps(bitmaps)


def app_icon() -> wx.Icon:
    icon = wx.Icon()
    icon.CopyFromBitmap(wx.Bitmap(_load("tray@2x.png")))
    return icon


def mark_tray_template() -> None:
    """macOS: flag the status item image as a template so the menu bar tints it (light/dark wallpaper).
    wx has no API for this; reach the NSStatusBarButton through PyObjC. Harmless if it fails."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp
    except ImportError:
        log.warning("PyObjC not available: tray icon will not adapt to the menu bar color")
        return

    def buttons(view):  # type: ignore[no-untyped-def]
        if view.className() == "NSStatusBarButton":
            yield view
        for child in view.subviews():
            yield from buttons(child)

    for window in NSApp.windows():
        if window.className() == "NSStatusBarWindow":
            for button in buttons(window.contentView()):
                if button.image() is not None:
                    button.image().setTemplate_(True)
                    button.setNeedsDisplay_(True)
