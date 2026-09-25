"""Spike S4: which tray click events does this platform deliver? Logs to spikes/s4_tray.log."""
import time, wx, wx.adv

LOG = open(__file__.replace(".py", ".log"), "w", buffering=1)
def log(msg): LOG.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

class Tray(wx.adv.TaskBarIcon):
    def __init__(self):
        super().__init__()
        bmp = wx.ArtProvider.GetBitmap(wx.ART_HARDDISK, wx.ART_OTHER, (32, 32))
        icon = wx.Icon(); icon.CopyFromBitmap(bmp)
        self.SetIcon(icon, "IRISFS spike")
        for ev, name in [(wx.adv.EVT_TASKBAR_LEFT_DOWN, "LEFT_DOWN"), (wx.adv.EVT_TASKBAR_LEFT_UP, "LEFT_UP"),
                         (wx.adv.EVT_TASKBAR_RIGHT_DOWN, "RIGHT_DOWN"), (wx.adv.EVT_TASKBAR_RIGHT_UP, "RIGHT_UP"),
                         (wx.adv.EVT_TASKBAR_CLICK, "CLICK")]:
            self.Bind(ev, lambda e, n=name: (log(f"event {n}"), e.Skip()))

    def CreatePopupMenu(self):
        log("CreatePopupMenu called")
        m = wx.Menu()
        m.AppendCheckItem(wx.ID_ANY, "Local IRIS").Check(True)
        m.AppendCheckItem(wx.ID_ANY, "Other server")
        m.AppendSeparator()
        m.Append(wx.ID_PREFERENCES, "Profiles…")
        q = m.Append(wx.ID_EXIT, "Quit")
        self.Bind(wx.EVT_MENU, lambda e: (log("quit"), wx.GetApp().ExitMainLoop()), q)
        return m

app = wx.App(False)
app.SetExitOnFrameDelete(False)
hidden = wx.Frame(None)  # never shown; keeps the main loop alive on macOS
tray = Tray()
log(f"started; platform={wx.PlatformInfo}; IsAvailable={wx.adv.TaskBarIcon.IsAvailable()}")
wx.CallLater(1_800_000, app.ExitMainLoop)  # auto-exit after 30 min
app.MainLoop()
tray.Destroy()
log("exited")
