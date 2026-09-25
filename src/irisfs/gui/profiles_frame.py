"""Profiles window (plan UI-7..UI-9): list on the left, form on the right, "+" and trash on the toolbar."""

from __future__ import annotations

import logging
import threading

import wx
import wx.adv

from irisfs import APP_NAME
from irisfs.config.profile import Profile
from irisfs.gui.controller import AppController, ProfileItem

log = logging.getLogger(__name__)
ERROR_BG = wx.Colour(255, 225, 225)
UNCHANGED = "(unchanged)"


class ProfilesFrame(wx.Frame):
    def __init__(self, controller: AppController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title=f"{APP_NAME} — Profiles", size=wx.Size(780, 520))
        self.controller = controller
        self.current: Profile | None = None
        self.current_is_new = False
        self.dirty = False
        self._loading = False
        self._items: list[ProfileItem] = []
        self._build()
        controller.add_listener(self._on_model_changed)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.reload_list(select_id=None)
        self.SetMinSize(wx.Size(640, 440))

    # ---- layout ------------------------------------------------------------------------------
    def _build(self) -> None:
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.TB_TEXT)
        art = wx.Size(24, 24)
        self.tool_add = tb.AddTool(
            wx.ID_ADD,
            "New",
            wx.ArtProvider.GetBitmapBundle(wx.ART_PLUS, wx.ART_TOOLBAR, art),
            shortHelp="New profile",
        )
        self.tool_delete = tb.AddTool(
            wx.ID_DELETE,
            "Delete",
            wx.ArtProvider.GetBitmapBundle(wx.ART_DELETE, wx.ART_TOOLBAR, art),
            shortHelp="Delete the selected profile",
        )
        tb.Realize()
        self.Bind(wx.EVT_TOOL, lambda _e: self.on_add(), id=wx.ID_ADD)
        self.Bind(wx.EVT_TOOL, lambda _e: self.on_delete(), id=wx.ID_DELETE)

        root = wx.Panel(self)
        self.list = wx.ListCtrl(root, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER)
        self.list.InsertColumn(0, "Profile")
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_select)
        self.list.Bind(
            wx.EVT_SIZE, lambda e: (self.list.SetColumnWidth(0, self.list.GetClientSize().width), e.Skip())
        )

        form = wx.Panel(root)
        self.form = form
        self.note = wx.StaticText(form, label="")
        self.note.SetForegroundColour(wx.Colour(160, 100, 0))
        self.name = wx.TextCtrl(form)
        self.host = wx.TextCtrl(form)
        self.host.SetHint("host name or IP address")
        self.port = wx.SpinCtrl(form, min=1, max=65535, initial=52773)
        self.user = wx.TextCtrl(form)
        self.password = wx.TextCtrl(form, style=wx.TE_PASSWORD)
        self.read_only = wx.CheckBox(form, label="Mount read-only")
        self.mount_point = wx.DirPickerCtrl(
            form, message="Choose the folder where the server appears", style=wx.DIRP_USE_TEXTCTRL
        )
        self.mount_point.SetToolTip(
            "macOS/Linux: an empty folder (created if missing).\n"
            "Windows: a drive letter (X:) or a folder that does not exist yet."
        )
        self.advanced = wx.CollapsiblePane(form, label="Advanced")
        adv = self.advanced.GetPane()
        self.https = wx.CheckBox(adv, label="Use HTTPS")
        self.verify_tls = wx.CheckBox(adv, label="Verify TLS certificate")
        self.prefix = wx.TextCtrl(adv)
        self.prefix.SetHint("e.g. /iris (web gateway prefix)")
        self.show_system = wx.CheckBox(adv, label="Show system items (%, library classes)")
        self.compile = wx.CheckBox(adv, label="Compile after import")
        adv_grid = wx.FlexGridSizer(cols=2, vgap=6, hgap=8)
        adv_grid.AddGrowableCol(1)
        adv_grid.AddMany(
            [
                (wx.StaticText(adv, label=""), 0),
                (self.https, 0),
                (wx.StaticText(adv, label=""), 0),
                (self.verify_tls, 0),
                (wx.StaticText(adv, label="URL prefix"), 0, wx.ALIGN_CENTER_VERTICAL),
                (self.prefix, 1, wx.EXPAND),
                (wx.StaticText(adv, label=""), 0),
                (self.show_system, 0),
                (wx.StaticText(adv, label=""), 0),
                (self.compile, 0),
            ]
        )
        adv.SetSizer(adv_grid)

        grid = wx.FlexGridSizer(cols=2, vgap=8, hgap=8)
        grid.AddGrowableCol(1)
        for label, ctrl in (
            ("Name", self.name),
            ("Server", self.host),
            ("Port", self.port),
            ("User", self.user),
            ("Password", self.password),
            ("", self.read_only),
            ("Mount folder", self.mount_point),
        ):
            grid.Add(wx.StaticText(form, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALIGN_RIGHT)
            grid.Add(ctrl, 1, wx.EXPAND)
        self.errors = wx.StaticText(form, label="")
        self.errors.SetForegroundColour(wx.Colour(190, 0, 0))
        self.btn_test = wx.Button(form, label="Test connection")
        self.btn_revert = wx.Button(form, label="Revert")
        self.btn_save = wx.Button(form, wx.ID_SAVE, label="Save")
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        buttons.Add(self.btn_test)
        buttons.AddStretchSpacer()
        buttons.Add(self.btn_revert, 0, wx.RIGHT, 8)
        buttons.Add(self.btn_save)

        col = wx.BoxSizer(wx.VERTICAL)
        col.Add(self.note, 0, wx.BOTTOM, 6)
        col.Add(grid, 0, wx.EXPAND)
        col.Add(self.advanced, 0, wx.EXPAND | wx.TOP, 8)
        col.Add(self.errors, 0, wx.EXPAND | wx.TOP, 8)
        col.AddStretchSpacer()
        col.Add(buttons, 0, wx.EXPAND | wx.TOP, 8)
        form.SetSizer(col)

        main = wx.BoxSizer(wx.HORIZONTAL)
        main.Add(self.list, 0, wx.EXPAND | wx.ALL, 10)
        main.Add(form, 1, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 10)
        self.list.SetMinSize(wx.Size(210, -1))
        root.SetSizer(main)

        self.fields = {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.user,
            "mount_point": self.mount_point,
            "path_prefix": self.prefix,
        }
        for ctrl in (self.name, self.host, self.user, self.password, self.prefix):
            ctrl.Bind(wx.EVT_TEXT, self._on_edit)
        self.port.Bind(wx.EVT_SPINCTRL, self._on_edit)
        self.port.Bind(wx.EVT_TEXT, self._on_edit)
        for cb in (self.read_only, self.https, self.verify_tls, self.show_system, self.compile):
            cb.Bind(wx.EVT_CHECKBOX, self._on_edit)
        self.mount_point.Bind(wx.EVT_DIRPICKER_CHANGED, self._on_edit)
        self.advanced.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, lambda _e: self.form.Layout())
        self.btn_save.Bind(wx.EVT_BUTTON, lambda _e: self.on_save())
        self.btn_revert.Bind(wx.EVT_BUTTON, lambda _e: self.on_revert())
        self.btn_test.Bind(wx.EVT_BUTTON, lambda _e: self.on_test())

    # ---- list --------------------------------------------------------------------------------
    def reload_list(self, select_id: str | None) -> None:
        self._items = self.controller.profile_items()
        self.list.DeleteAllItems()
        rows = [(i.id, ("● " if i.active else "○ ") + i.label) for i in self._items]
        if self.current_is_new and self.current is not None:
            rows.append((self.current.id, "✚ " + self.current.name))
        for index, (_, text) in enumerate(rows):
            self.list.InsertItem(index, text)
        target = select_id or (self.current.id if self.current else None)
        ids = [r[0] for r in rows]
        if target in ids:
            self._select_row(ids.index(target))
        elif rows:
            self._select_row(0)
        else:
            self._show(None, is_new=False)

    def _select_row(self, index: int) -> None:
        self.list.Select(index)
        self.list.Focus(index)
        self.list.EnsureVisible(index)

    def _row_id(self, index: int) -> str | None:
        if 0 <= index < len(self._items):
            return self._items[index].id
        if self.current_is_new and self.current is not None and index == len(self._items):
            return self.current.id
        return None

    def _on_select(self, event: wx.ListEvent) -> None:
        profile_id = self._row_id(event.GetIndex())
        if profile_id is None or (self.current is not None and profile_id == self.current.id):
            self._update_enabled()
            return
        if not self._confirm_discard():
            wx.CallAfter(self.reload_list, self.current.id if self.current else None)
            return
        self.current_is_new = False
        self._show(self.controller.store.get(profile_id), is_new=False)
        wx.CallAfter(self.reload_list, profile_id)

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
            self.port.SetValue(p.port if p else 52773)
            self.user.SetValue(p.username if p else "")
            has_password = bool(p) and not is_new and self.controller.store.password(p.id) is not None  # type: ignore[union-attr]
            self.password.SetValue("")
            self.password.SetHint(UNCHANGED if has_password else "password")
            self.read_only.SetValue(p.read_only if p else False)
            self.mount_point.SetPath(p.mount_point if p else "")
            self.https.SetValue(p.https if p else False)
            self.verify_tls.SetValue(p.verify_tls if p else True)
            self.prefix.SetValue(p.path_prefix if p else "")
            self.show_system.SetValue(p.show_system if p else False)
            self.compile.SetValue(p.compile_on_import if p else True)
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
            port=self.port.GetValue(),
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

    def _form_password(self) -> str | None:
        value = self.password.GetValue()
        return value if value or self.current_is_new else None  # empty = keep the saved password

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
            self.read_only,
            self.mount_point,
            self.https,
            self.verify_tls,
            self.prefix,
            self.show_system,
            self.compile,
            self.btn_save,
            self.btn_revert,
        ):
            ctrl.Enable(has and not locked)
        self.btn_test.Enable(has)
        self.btn_save.Enable(has and not locked and self.dirty)
        self.btn_revert.Enable(has and not locked and self.dirty and not self.current_is_new)
        self.GetToolBar().EnableTool(wx.ID_DELETE, has and not locked)
        self.note.SetLabel(
            "Mounted — unmount it from the menu bar icon to edit or delete it." if locked else ""
        )
        self.form.Layout()

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
        self.errors.SetLabel("\n".join(errors.values()))
        self.errors.Wrap(max(200, self.form.GetClientSize().width - 20))
        self.form.Layout()

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

        def work() -> None:
            ok, message = self.controller.test_connection(profile, password)
            wx.CallAfter(done, ok, message)

        def done(ok: bool, message: str) -> None:
            if not self:
                return
            self.btn_test.SetLabel("Test connection")
            self.btn_test.Enable()
            if ok:
                self.controller.prompter.info("Connection OK", message)
            else:
                self.controller.prompter.error("Connection failed", message)

        threading.Thread(target=work, daemon=True).start()

    def _on_close(self, event: wx.CloseEvent) -> None:
        if event.CanVeto() and not self._confirm_discard():
            event.Veto()
            return
        self.controller.remove_listener(self._on_model_changed)
        self.Destroy()
