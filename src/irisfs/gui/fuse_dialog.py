"""'FUSE is needed' dialog: tailored install instructions (commands to copy, download links, Check again)."""

from __future__ import annotations

from collections.abc import Callable

import wx
import wx.adv

from irisfs import APP_NAME
from irisfs.mount.fuse_help import Advice


def _label(text: str) -> str:
    """wx treats '&' in labels as a mnemonic marker ("Privacy & Security" would lose its '&')."""
    return text.replace("&", "&&")


class FuseMissingDialog(wx.Dialog):
    def __init__(
        self, advice: Advice, recheck: Callable[[], Advice | None], parent: wx.Window | None = None
    ) -> None:
        super().__init__(
            parent, title=f"{APP_NAME} — {advice.title}", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.recheck = recheck
        self.advice = advice
        panel = wx.Panel(self)
        col = wx.BoxSizer(wx.VERTICAL)

        heading = wx.StaticText(panel, label=_label(advice.title))
        font = heading.GetFont()
        font.SetPointSize(font.GetPointSize() + 3)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        heading.SetFont(font)
        col.Add(heading, 0, wx.BOTTOM, 8)

        self.intro = wx.StaticText(panel, label=_label(advice.intro))
        self.intro.Wrap(520)
        col.Add(self.intro, 0, wx.BOTTOM, 10)

        self.commands = None
        if advice.commands:
            row = wx.BoxSizer(wx.HORIZONTAL)
            self.commands = wx.TextCtrl(
                panel,
                value="\n".join(advice.commands),
                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
                size=wx.Size(-1, 22 * max(2, len(advice.commands)) + 8),
            )
            self.commands.SetFont(
                wx.Font(wx.FontInfo(font.GetPointSize() - 3).Family(wx.FONTFAMILY_TELETYPE))
            )
            copy = wx.Button(panel, label="Copy")
            copy.SetToolTip("Copy the commands to the clipboard")
            copy.Bind(wx.EVT_BUTTON, self._copy)
            row.Add(self.commands, 1, wx.EXPAND | wx.RIGHT, 6)
            row.Add(copy, 0, wx.ALIGN_TOP)
            col.Add(row, 0, wx.EXPAND | wx.BOTTOM, 10)

        self.links: list[wx.adv.HyperlinkCtrl] = []
        for label, url in advice.links:
            link = wx.adv.HyperlinkCtrl(panel, label=label, url=url)
            self.links.append(link)
            col.Add(link, 0, wx.BOTTOM, 4)

        for note in advice.notes:
            text = wx.StaticText(panel, label=_label(note))
            text.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
            text.Wrap(520)
            col.Add(text, 0, wx.TOP, 8)

        self.status = wx.StaticText(panel, label="")
        col.Add(self.status, 0, wx.TOP, 8)

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.AddStretchSpacer()
        self.btn_check = wx.Button(panel, label="Check again")
        self.btn_close = wx.Button(panel, wx.ID_CLOSE, label="Close")
        self.btn_check.Bind(wx.EVT_BUTTON, lambda _e: self.on_check())
        self.btn_close.Bind(wx.EVT_BUTTON, lambda _e: self.Close())
        buttons.Add(self.btn_check, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_close)
        col.Add(buttons, 0, wx.EXPAND | wx.TOP, 14)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(col, 1, wx.EXPAND | wx.ALL, 18)
        panel.SetSizer(outer)
        frame_sizer = wx.BoxSizer(wx.VERTICAL)
        frame_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(frame_sizer)
        self.Bind(wx.EVT_CLOSE, lambda _e: self.Destroy())

    def _copy(self, _event: wx.Event) -> None:
        commands = [c for c in self.advice.commands if not c.startswith("#")]
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject("\n".join(commands)))
            finally:
                wx.TheClipboard.Close()
            self.status.SetLabel("Copied to the clipboard.")

    def on_check(self) -> bool:
        """True (and the dialog closes) once a usable FUSE driver is found."""
        if self.recheck() is None:
            self.status.SetForegroundColour(wx.Colour(30, 110, 55))
            self.status.SetLabel("✓ FUSE found. You can mount servers now.")
            wx.CallLater(900, lambda: self and self.Close())
            return True
        self.status.SetForegroundColour(wx.Colour(170, 20, 20))
        self.status.SetLabel("Still not found. After installing, macOS/Windows may need a restart.")
        self.Layout()
        return False
