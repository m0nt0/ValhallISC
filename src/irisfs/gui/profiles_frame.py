"""Profiles window (plan UI-7..UI-9, reworked after design review 1).

Left: two-line profile rows (status dot, name, host or mount state) with "+" and trash buttons below.
Right: a banner with Open folder / Unmount while the profile is mounted, then two tabs - Connection
(grouped: Server, Sign in, Mount) and Options - and a fixed footer: Test connection (+ inline result),
Revert, Save.
"""

from __future__ import annotations

import html
import logging
import threading

import wx
import wx.html
import wx.lib.scrolledpanel

from irisfs import APP_NAME
from irisfs.config.profile import Profile
from irisfs.gui import icons
from irisfs.gui.controller import AppController, ProfileItem

log = logging.getLogger(__name__)
ERROR_BG = wx.Colour(255, 225, 225)
ERROR_FG = wx.Colour(170, 20, 20)
OK_FG = wx.Colour(30, 110, 55)
UNCHANGED = "Saved — type to replace"
DOT = {"mounted": "#1f8a45", "busy": "#c58a12", "idle": "#8a867c", "new": "#0b57b8"}

EYE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none" '
    'stroke="{c}" stroke-width="1.4"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/>'
    '<circle cx="8" cy="8" r="2"/></svg>'
)


def _row_html(name: str, subtitle: str, dot: str, *, italic: bool = False, selected: bool = False) -> str:
    if selected:  # on the selection colour, everything uses the selection text colour (contrast)
        text = wx.SystemSettings.GetColour(wx.SYS_COLOUR_HIGHLIGHTTEXT).GetAsString(wx.C2S_HTML_SYNTAX)
        grey = dot = text
    else:
        text = wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOXTEXT).GetAsString(wx.C2S_HTML_SYNTAX)
        grey = wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT).GetAsString(wx.C2S_HTML_SYNTAX)
    title = f"<i>{html.escape(name)}</i>" if italic else html.escape(name)
    return (
        f'<font color="{dot}">●</font>&nbsp;<font color="{text}"><b>{title}</b></font><br>'
        f'&nbsp;&nbsp;&nbsp;&nbsp;<font size="-1" color="{grey}">{html.escape(subtitle)}</font>'
    )


def _heading(parent: wx.Window, text: str) -> wx.StaticText:
    label = wx.StaticText(parent, label=text.upper())
    font = label.GetFont()
    font.SetPointSize(max(8, font.GetPointSize() - 2))
    font.SetWeight(wx.FONTWEIGHT_BOLD)
    label.SetFont(font)
    label.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
    return label


class ProfilesFrame(wx.Frame):
    def __init__(self, controller: AppController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title=f"{APP_NAME} — Profiles", size=wx.Size(900, 600))
        self.controller = controller
        self.current: Profile | None = None
        self.current_is_new = False
        self.dirty = False
        self._loading = False
        self._rows: list[str] = []  # profile id per list row
        self._items: dict[str, ProfileItem] = {}
        self._build()
        controller.add_listener(self._on_model_changed)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.reload_list(select_id=None)
        self.SetMinSize(wx.Size(760, 520))

    # ---- layout ------------------------------------------------------------------------------
    def _build(self) -> None:
        root = wx.Panel(self)
        self.root = root

        # left: list + buttons
        side = wx.Panel(root)
        self.list = wx.html.SimpleHtmlListBox(side, style=wx.html.HLB_DEFAULT_STYLE | wx.BORDER_THEME)
        self.list.Bind(wx.EVT_LISTBOX, lambda e: self.select_row(e.GetSelection()))
        self.btn_add = wx.BitmapButton(side, bitmap=icons.svg_bundle(icons.PLUS_SVG, 16))
        self.btn_add.SetToolTip("New profile")
        self.btn_delete = wx.BitmapButton(side, bitmap=icons.svg_bundle(icons.TRASH_SVG, 16))
        self.btn_delete.SetToolTip("Delete the selected profile")
        self.btn_add.Bind(wx.EVT_BUTTON, lambda _e: self.on_add())
        self.btn_delete.Bind(wx.EVT_BUTTON, lambda _e: self.on_delete())
        side_buttons = wx.BoxSizer(wx.HORIZONTAL)
        side_buttons.Add(self.btn_add, 0, wx.RIGHT, 4)
        side_buttons.Add(self.btn_delete)
        side_sizer = wx.BoxSizer(wx.VERTICAL)
        side_sizer.Add(self.list, 1, wx.EXPAND)
        side_sizer.Add(side_buttons, 0, wx.TOP, 6)
        side.SetSizer(side_sizer)
        side.SetMinSize(wx.Size(260, -1))

        # right: banner, tabs, footer
        form = wx.Panel(root)
        self.form = form
        self.banner = wx.Panel(form)
        dark = wx.SystemSettings.GetAppearance().IsDark()
        # soft green matching the "mounted" dot (the system "info" colour is a dark tooltip grey on GTK)
        self.banner.SetBackgroundColour(wx.Colour(28, 60, 40) if dark else wx.Colour(228, 243, 233))
        self.banner_text = wx.StaticText(self.banner, label="")
        self.banner_text.SetForegroundColour(wx.Colour(200, 235, 210) if dark else wx.Colour(20, 70, 38))
        self.btn_open = wx.Button(self.banner, label="Open folder")
        self.btn_unmount = wx.Button(self.banner, label="Unmount…")
        self.btn_open.Bind(
            wx.EVT_BUTTON, lambda _e: self.current and self.controller.open_folder(self.current.id)
        )
        self.btn_unmount.Bind(wx.EVT_BUTTON, lambda _e: self.on_unmount())
        banner_sizer = wx.BoxSizer(wx.HORIZONTAL)
        banner_sizer.Add(self.banner_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 10)
        banner_sizer.Add(self.btn_open, 0, wx.ALIGN_CENTER_VERTICAL | wx.TOP | wx.BOTTOM, 6)
        banner_sizer.Add(self.btn_unmount, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 6)
        self.banner.SetSizer(banner_sizer)
        self.banner.Hide()

        self.tabs = wx.Notebook(form)
        conn = wx.lib.scrolledpanel.ScrolledPanel(self.tabs, style=wx.TAB_TRAVERSAL)
        opts = wx.Panel(self.tabs)
        self.fields_panel = conn
        self.tabs.AddPage(conn, "Connection")
        self.tabs.AddPage(opts, "Options")
        self.field_errors: dict[str, wx.StaticText] = {}

        def field(parent: wx.Window, label: str, ctrl: wx.Window | wx.Sizer, key: str = "") -> wx.BoxSizer:
            box = wx.BoxSizer(wx.VERTICAL)
            if label:
                caption = wx.StaticText(parent, label=label)
                caption.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
                box.Add(caption, 0, wx.BOTTOM, 3)
            box.Add(ctrl, 0, wx.EXPAND)
            if key:
                message = wx.StaticText(parent, label="")
                message.SetForegroundColour(ERROR_FG)
                message.Hide()
                box.Add(message, 0, wx.TOP, 2)
                self.field_errors[key] = message
            return box

        # Connection tab
        self.name = wx.TextCtrl(conn)
        self.host = wx.TextCtrl(conn)
        self.host.SetHint("host name or IP address")
        self.port = wx.TextCtrl(conn, value="52773", size=wx.Size(90, -1))
        self.https = wx.CheckBox(conn, label="Use HTTPS")
        self.verify_tls = wx.CheckBox(conn, label="Verify certificate")
        self.user = wx.TextCtrl(conn)
        self.password = wx.TextCtrl(conn, style=wx.TE_PASSWORD)
        self.password_plain = wx.TextCtrl(conn)  # shown instead of `password` while "show" is on
        self.password_plain.Hide()
        self.btn_eye = wx.BitmapToggleButton(
            conn, label=icons.svg_bundle(EYE_SVG, 16).GetBitmap(wx.Size(16, 16))
        )
        self.btn_eye.SetToolTip("Show password")
        self.btn_eye.Bind(wx.EVT_TOGGLEBUTTON, lambda _e: self._toggle_password())
        self.mount_point = wx.DirPickerCtrl(
            conn, message="Choose the folder where the server appears", style=wx.DIRP_USE_TEXTCTRL
        )
        self.mount_point.SetToolTip(
            "macOS/Linux: an empty folder (created if missing).\n"
            "Windows: a drive letter (X:) or a folder that does not exist yet."
        )
        self.read_only = wx.CheckBox(conn, label="Read-only (browse and copy out, never import)")

        server_row = wx.BoxSizer(wx.HORIZONTAL)
        server_row.Add(field(conn, "Address", self.host, "host"), 1, wx.RIGHT, 12)
        server_row.Add(field(conn, "Port", self.port, "port"), 0)
        tls_row = wx.BoxSizer(wx.HORIZONTAL)
        tls_row.Add(self.https, 0, wx.RIGHT, 20)
        tls_row.Add(self.verify_tls)
        pw_row = wx.BoxSizer(wx.HORIZONTAL)
        pw_row.Add(self.password, 1, wx.EXPAND)
        pw_row.Add(self.password_plain, 1, wx.EXPAND)
        pw_row.Add(self.btn_eye, 0, wx.LEFT, 4)
        sign_row = wx.BoxSizer(wx.HORIZONTAL)
        sign_row.Add(field(conn, "User", self.user, "username"), 1, wx.RIGHT, 12)
        sign_row.Add(field(conn, "Password", pw_row), 1)

        self.errors = wx.StaticText(conn, label="")
        self.errors.SetForegroundColour(ERROR_FG)
        col = wx.BoxSizer(wx.VERTICAL)
        col.Add(field(conn, "Profile name", self.name, "name"), 0, wx.EXPAND | wx.BOTTOM, 16)
        col.Add(_heading(conn, "Server"), 0, wx.BOTTOM, 6)
        col.Add(server_row, 0, wx.EXPAND | wx.BOTTOM, 6)
        col.Add(tls_row, 0, wx.BOTTOM, 16)
        col.Add(_heading(conn, "Sign in"), 0, wx.BOTTOM, 6)
        col.Add(sign_row, 0, wx.EXPAND | wx.BOTTOM, 16)
        col.Add(_heading(conn, "Mount"), 0, wx.BOTTOM, 6)
        col.Add(field(conn, "Folder", self.mount_point, "mount_point"), 0, wx.EXPAND | wx.BOTTOM, 6)
        col.Add(self.read_only, 0, wx.BOTTOM, 8)
        col.Add(self.errors, 0, wx.EXPAND)
        padded = wx.BoxSizer(wx.VERTICAL)
        padded.Add(col, 1, wx.EXPAND | wx.ALL, 14)
        conn.SetSizer(padded)
        conn.SetupScrolling(scroll_x=False, rate_y=12)

        # Options tab
        self.prefix = wx.TextCtrl(opts)
        self.prefix.SetHint("e.g. /iris (web gateway prefix)")
        self.show_system = wx.CheckBox(opts, label="Show system items (%, library classes)")
        self.compile = wx.CheckBox(opts, label="Compile after import")
        ocol = wx.BoxSizer(wx.VERTICAL)
        ocol.Add(field(opts, "URL prefix", self.prefix, "path_prefix"), 0, wx.EXPAND | wx.BOTTOM, 14)
        ocol.Add(self.show_system, 0, wx.BOTTOM, 8)
        ocol.Add(self.compile)
        opadded = wx.BoxSizer(wx.VERTICAL)
        opadded.Add(ocol, 1, wx.EXPAND | wx.ALL, 14)
        opts.SetSizer(opadded)

        # footer
        self.btn_test = wx.Button(form, label="Test connection")
        self.test_result = wx.StaticText(form, label="")
        self.btn_revert = wx.Button(form, label="Revert")
        self.btn_save = wx.Button(form, wx.ID_SAVE, label="Save")
        footer = wx.BoxSizer(wx.HORIZONTAL)
        footer.Add(self.btn_test)
        footer.Add(self.test_result, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        footer.Add(self.btn_revert, 0, wx.RIGHT, 8)
        footer.Add(self.btn_save)

        fcol = wx.BoxSizer(wx.VERTICAL)
        fcol.Add(self.banner, 0, wx.EXPAND | wx.BOTTOM, 8)
        fcol.Add(self.tabs, 1, wx.EXPAND)
        fcol.Add(footer, 0, wx.EXPAND | wx.TOP, 10)
        form.SetSizer(fcol)

        main = wx.BoxSizer(wx.HORIZONTAL)
        main.Add(side, 0, wx.EXPAND | wx.ALL, 10)
        main.Add(form, 1, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 10)
        root.SetSizer(main)

        self.fields = {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.user,
            "mount_point": self.mount_point,
            "path_prefix": self.prefix,
        }
        for ctrl in (
            self.name,
            self.host,
            self.port,
            self.user,
            self.password,
            self.password_plain,
            self.prefix,
        ):
            ctrl.Bind(wx.EVT_TEXT, self._on_edit)
        for cb in (self.read_only, self.https, self.verify_tls, self.show_system, self.compile):
            cb.Bind(wx.EVT_CHECKBOX, self._on_edit)
        self.mount_point.Bind(wx.EVT_DIRPICKER_CHANGED, self._on_edit)
        self.btn_save.Bind(wx.EVT_BUTTON, lambda _e: self.on_save())
        self.btn_revert.Bind(wx.EVT_BUTTON, lambda _e: self.on_revert())
        self.btn_test.Bind(wx.EVT_BUTTON, lambda _e: self.on_test())

    # ---- list --------------------------------------------------------------------------------
    def reload_list(self, select_id: str | None) -> None:
        items = self.controller.profile_items()
        self._items = {i.id: i for i in items}
        self._rows = [i.id for i in items]
        if self.current_is_new and self.current is not None:
            self._rows.append(self.current.id)
        target = select_id or (self.current.id if self.current else None)
        if target in self._rows:
            self._render_rows(self._rows.index(target))
            if self.current is None or self.current.id != target:
                self._show(self.controller.store.get(target), is_new=False)
        elif self._rows:
            self._render_rows(0)
            self.select_row(0)
        else:
            self._render_rows(-1)
            self._show(None, is_new=False)
        self._update_enabled()

    def _render_rows(self, selected: int) -> None:
        """Rows are re-rendered on selection so the selected one uses the selection text colour."""
        html_rows = []
        for index, profile_id in enumerate(self._rows):
            sel = index == selected
            item = self._items.get(profile_id)
            if item is not None:
                dot = DOT["mounted"] if item.mounted else DOT["busy"] if item.active else DOT["idle"]
                html_rows.append(_row_html(item.name, item.subtitle, dot, selected=sel))
            elif self.current is not None:  # the unsaved new profile
                name = self.current.name or "New server"
                html_rows.append(_row_html(name, "not saved yet", DOT["new"], italic=True, selected=sel))
        self.list.Set(html_rows)
        if 0 <= selected < len(html_rows):
            self.list.SetSelection(selected)

    def row_count(self) -> int:
        return len(self._rows)

    def select_row(self, index: int) -> None:
        """Show the profile of list row `index` (asks before discarding unsaved changes)."""
        if not 0 <= index < len(self._rows):
            return
        profile_id = self._rows[index]
        if self.current is not None and profile_id == self.current.id:
            self._update_enabled()
            return
        if not self._confirm_discard():
            if self.current is not None and self.current.id in self._rows:
                self._render_rows(self._rows.index(self.current.id))
            return
        drop_new = self.current_is_new
        self.current_is_new = False
        self._show(self.controller.store.get(profile_id), is_new=False)
        if drop_new:
            wx.CallAfter(self.reload_list, profile_id)
        else:
            self._render_rows(index)

    def _on_model_changed(self) -> None:
        if self:  # window may already be destroyed
            self.reload_list(self.current.id if self.current else None)

    # ---- form --------------------------------------------------------------------------------
    def _show(self, profile: Profile | None, *, is_new: bool) -> None:
        self._loading = True
        try:
            self.current = profile
            self.current_is_new = is_new
            self.dirty = is_new
            p = profile
            self.name.SetValue(p.name if p else "")
            self.host.SetValue(p.host if p else "")
            self.port.SetValue(str(p.port if p else 52773))
            self.user.SetValue(p.username if p else "")
            has_password = p is not None and not is_new and self.controller.store.password(p.id) is not None
            for ctrl in (self.password, self.password_plain):
                ctrl.SetValue("")
                ctrl.SetHint(UNCHANGED if has_password else "password")
            self.read_only.SetValue(p.read_only if p else False)
            self.mount_point.SetPath(p.mount_point if p else "")
            self.https.SetValue(p.https if p else False)
            self.verify_tls.SetValue(p.verify_tls if p else True)
            self.prefix.SetValue(p.path_prefix if p else "")
            self.show_system.SetValue(p.show_system if p else False)
            self.compile.SetValue(p.compile_on_import if p else True)
            self.test_result.SetLabel("")
            self._set_errors({})
        finally:
            self._loading = False
        self._update_enabled()

    def form_profile(self) -> Profile | None:
        """The profile as currently edited in the form."""
        if self.current is None:
            return None
        return Profile(
            id=self.current.id,
            name=self.name.GetValue().strip(),
            host=self.host.GetValue().strip(),
            port=self._port_value(),
            username=self.user.GetValue().strip(),
            mount_point=self.mount_point.GetPath().strip(),
            read_only=self.read_only.GetValue(),
            https=self.https.GetValue(),
            verify_tls=self.verify_tls.GetValue(),
            path_prefix=self.prefix.GetValue().strip(),
            show_system=self.show_system.GetValue(),
            compile_on_import=self.compile.GetValue(),
            compile_flags=self.current.compile_flags,
        )

    def _port_value(self) -> int:
        try:
            return int(self.port.GetValue().strip())
        except ValueError:
            return 0  # reported by validation as "Port must be between 1 and 65535"

    def _toggle_password(self) -> None:
        show = self.btn_eye.GetValue()
        source, target = (
            (self.password, self.password_plain) if show else (self.password_plain, self.password)
        )
        self._loading = True
        try:
            target.SetValue(source.GetValue())
        finally:
            self._loading = False
        source.Hide()
        target.Show()
        self.btn_eye.SetToolTip("Hide password" if show else "Show password")
        self._fit_contents()

    def _form_password(self) -> str | None:
        value = (self.password_plain if self.password_plain.IsShown() else self.password).GetValue()
        return value if value or self.current_is_new else None  # empty = keep the saved password

    def _fit_contents(self) -> None:
        """Recompute the scrollable field area after its content changed (error lines, password toggle)."""
        self.fields_panel.Layout()
        self.fields_panel.FitInside()
        self.form.Layout()

    def is_read_only(self) -> bool:
        return (
            self.current is not None
            and not self.current_is_new
            and self.controller.manager.is_active(self.current.id)
        )

    def _update_enabled(self) -> None:
        locked = self.is_read_only()
        has = self.current is not None
        for ctrl in (
            self.name,
            self.host,
            self.port,
            self.user,
            self.password,
            self.password_plain,
            self.btn_eye,
            self.read_only,
            self.mount_point,
            self.https,
            self.verify_tls,
            self.prefix,
            self.show_system,
            self.compile,
        ):
            ctrl.Enable(has and not locked)
        self.btn_test.Enable(has)
        self.btn_save.Enable(has and not locked and self.dirty)
        self.btn_revert.Enable(has and not locked and self.dirty and not self.current_is_new)
        self.btn_delete.Enable(has and not locked)
        self._update_banner(locked)
        self.form.Layout()

    def _update_banner(self, locked: bool) -> None:
        """Design review #5: a mounted profile gets its actions right here instead of a dead end."""
        item = self._items.get(self.current.id) if self.current else None
        if not locked or item is None:
            self.banner.Hide()
            return
        if item.mounted:
            self.banner_text.SetLabel(f"{item.subtitle}. Unmount to edit or delete this profile.")
        else:
            self.banner_text.SetLabel(f"{item.status} Editing is available once it has finished.")
        self.btn_open.Show(item.mounted)
        self.btn_unmount.Show(item.mounted)
        self.banner.Show()
        self.banner.Layout()

    def _on_edit(self, event: wx.Event) -> None:
        event.Skip()
        if not self._loading and self.current is not None and not self.dirty:
            self.dirty = True
            self._update_enabled()

    def _set_errors(self, errors: dict[str, str]) -> None:
        for field_name, ctrl in self.fields.items():
            target = ctrl.GetTextCtrl() if isinstance(ctrl, wx.DirPickerCtrl) else ctrl
            if target is not None:
                target.SetBackgroundColour(ERROR_BG if field_name in errors else wx.NullColour)
                target.Refresh()
        for field_name, label in self.field_errors.items():
            label.SetLabel(errors.get(field_name, ""))
            label.Show(field_name in errors)
        rest = [msg for name, msg in errors.items() if name not in self.field_errors]
        self.errors.SetLabel("\n".join(rest))
        self.errors.Wrap(max(200, self.fields_panel.GetClientSize().width - 20))
        if "path_prefix" in errors and len(errors) == 1:
            self.tabs.SetSelection(1)  # the only problem is on the Options tab: show it
        elif errors:
            self.tabs.SetSelection(0)
        self._fit_contents()

    # ---- actions -----------------------------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.dirty or self.current is None:
            return True
        return self.controller.prompter.confirm(
            "Unsaved changes",
            f"Discard the changes to “{self.current.name}”?",
            yes="Discard",
            no="Keep editing",
        )

    def on_add(self) -> None:
        if not self._confirm_discard():
            return
        self._show(self.controller.new_profile(), is_new=True)
        self.tabs.SetSelection(0)
        self.reload_list(self.current.id if self.current else None)
        wx.CallAfter(self._focus_name)

    def _focus_name(self) -> None:
        # Deferred and guarded: on Windows, SetFocus on a window that is not (yet) shown logs
        # "'SetFocus' failed with error 0x57".
        if self and self.IsShown() and self.name.IsEnabled():
            self.name.SetFocus()
            self.name.SelectAll()

    def on_delete(self) -> None:
        if self.current is None:
            return
        if self.current_is_new:
            self.current, self.current_is_new, self.dirty = None, False, False
            self.reload_list(None)
            return
        if self.controller.delete_profile(self.current.id):
            self.current, self.dirty = None, False
            self.reload_list(None)

    def on_unmount(self) -> None:
        if self.current is not None:
            self.controller.on_profile_clicked(self.current.id)  # asks for confirmation (spec)

    def on_save(self) -> bool:
        profile = self.form_profile()
        if profile is None:
            return False
        errors = self.controller.save_profile(profile, self._form_password())
        self._set_errors(errors)
        if errors:
            return False
        self.current_is_new = False
        self.dirty = False
        self._show(self.controller.store.get(profile.id), is_new=False)
        self.reload_list(profile.id)
        return True

    def on_revert(self) -> None:
        if self.current is not None and not self.current_is_new:
            self._show(self.controller.store.get(self.current.id), is_new=False)

    def on_test(self) -> None:
        profile = self.form_profile()
        if profile is None:
            return
        errors = {k: v for k, v in profile.validate(self.controller.system).items() if k != "mount_point"}
        if errors:
            self._set_errors(errors)
            return
        password = self._form_password()
        self.btn_test.Disable()
        self.btn_test.SetLabel("Testing…")
        self.test_result.SetLabel("")

        def work() -> None:
            ok, message = self.controller.test_connection(profile, password)
            wx.CallAfter(done, ok, message)

        def done(ok: bool, message: str) -> None:
            if not self:
                return
            self.btn_test.SetLabel("Test connection")
            self.btn_test.Enable()
            first_line = message.splitlines()[0] if message else ""
            self.test_result.SetForegroundColour(OK_FG if ok else ERROR_FG)
            self.test_result.SetLabel(("✓ " if ok else "✗ ") + first_line)
            self.test_result.SetToolTip(message)
            self.form.Layout()

        threading.Thread(target=work, daemon=True).start()

    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self._confirm_discard():
            event.Veto()
            return
        self.controller.remove_listener(self._on_model_changed)
        self.Destroy()
