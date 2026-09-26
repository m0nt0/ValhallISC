"""Spike S4b: monochrome tray icon drawn at runtime, flagged as a macOS template image."""
import sys, time, wx, wx.adv

LOG = open(__file__.replace(".py", ".log"), "w", buffering=1)
def log(m): LOG.write(f"{time.strftime('%H:%M:%S')} {m}\n")

def draw_icon(px: int, active: bool) -> wx.Bitmap:
    """Black glyph on transparent background: a database cylinder (filled when active)."""
    bmp = wx.Bitmap.FromRGBA(px, px, 0, 0, 0, 0)
    dc = wx.MemoryDC(bmp); gc = wx.GraphicsContext.Create(dc)
    s = px / 18.0; lw = 1.6 * s
    x0, x1, top, bot, eh = 3 * s, 15 * s, 2.5 * s, 15.5 * s, 4 * s
    black = wx.Colour(0, 0, 0, 255)
    gc.SetPen(gc.CreatePen(wx.GraphicsPenInfo(black).Width(lw)))
    gc.SetBrush(wx.Brush(black) if active else wx.TRANSPARENT_BRUSH)
    body = gc.CreatePath()
    body.MoveToPoint(x0, top + eh / 2); body.AddLineToPoint(x0, bot - eh / 2)
    body.AddArc(px / 2, bot - eh / 2, (x1 - x0) / 2, 3.1416, 0, False)  # approximate bottom
    body.AddLineToPoint(x1, top + eh / 2)
    gc.DrawPath(body) if active else gc.StrokePath(body)
    gc.SetBrush(wx.TRANSPARENT_BRUSH if not active else wx.Brush(black))
    gc.DrawEllipse(x0, top, x1 - x0, eh)
    if not active:
        gc.StrokeLine(x0, (top + bot) / 2, x1, (top + bot) / 2)
    del gc; dc.SelectObject(wx.NullBitmap)
    return bmp

def mark_template():
    """Flag the status item image as template so macOS tints it for the menu bar background."""
    from AppKit import NSApp
    def buttons(v):
        if v.className() == "NSStatusBarButton":
            yield v
        for c in v.subviews():
            yield from buttons(c)
    n = 0
    for w in NSApp.windows():
        if w.className() == "NSStatusBarWindow":
            for b in buttons(w.contentView()):
                if b.image() is not None:
                    b.image().setTemplate_(True); b.setNeedsDisplay_(True); n += 1
    log(f"template-flagged {n} image(s)")

class Tray(wx.adv.TaskBarIcon):
    def __init__(self):
        super().__init__(); self.active = False; self.update()
    def update(self):
        bundle = wx.BitmapBundle.FromBitmaps([draw_icon(18, self.active), draw_icon(36, self.active)])
        self.SetIcon(bundle, "IRISFS spike")
        wx.CallAfter(mark_template)
    def CreatePopupMenu(self):
        m = wx.Menu()
        t = m.Append(wx.ID_ANY, "Toggle active/inactive icon")
        self.Bind(wx.EVT_MENU, lambda e: (setattr(self, "active", not self.active), self.update()), t)
        q = m.Append(wx.ID_EXIT, "Quit"); self.Bind(wx.EVT_MENU, lambda e: wx.GetApp().ExitMainLoop(), q)
        return m

app = wx.App(False); hidden = wx.Frame(None); tray = Tray()
wx.CallLater(1_800_000, app.ExitMainLoop); app.MainLoop(); tray.Destroy()
