"""Application logic behind the tray icon and the Profiles window (no wx imports: unit-testable).

Threading: every public method is called on the UI thread. Slow work goes through `run_bg`, and results
come back through `call_ui` (wx.CallAfter in the app, immediate calls in tests).
"""

from __future__ import annotations

import logging
import os
import platform
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from irisfs import APP_NAME
from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AtelierError
from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.config.store import ProfileActiveError, ProfileStore
from irisfs.gui.prompter import Prompter
from irisfs.mount import fuselib, protocol
from irisfs.mount.manager import Change, MountError, MountManager, State

log = logging.getLogger(__name__)
Task = Callable[[], None]

_ERROR_TEXT = {
    protocol.AUTH_FAILED: "The server rejected the user name or password.",
    protocol.UNREACHABLE: "The server could not be reached. Check address and port and that IRIS is running.",
    protocol.SERVER_ERROR: "The server returned an error.",
    protocol.FUSE_MISSING: "No FUSE driver is installed on this computer.",
    protocol.MOUNTPOINT_INVALID: "The mount folder cannot be used.",
    protocol.MOUNT_FAILED: "The folder could not be mounted.",
    protocol.BAD_CONFIG: "The profile is not valid.",
    "TIMEOUT": "Mounting took too long and was cancelled.",
}


@dataclass(frozen=True)
class ProfileItem:
    id: str
    name: str
    state: State
    mount_point: str

    @property
    def active(self) -> bool:
        return self.state is not State.INACTIVE

    @property
    def label(self) -> str:
        if self.state is State.MOUNTING:
            return f"{self.name} (connecting…)"
        if self.state is State.UNMOUNTING:
            return f"{self.name} (unmounting…)"
        return self.name

    @property
    def enabled(self) -> bool:
        return self.state in (State.ACTIVE, State.INACTIVE)


def _thread(task: Task) -> None:
    threading.Thread(target=task, daemon=True).start()


class AppController:
    def __init__(
        self,
        store: ProfileStore,
        manager: MountManager,
        prompter: Prompter,
        *,
        request_exit: Task,
        call_ui: Callable[[Task], None],
        run_bg: Callable[[Task], None] = _thread,
        system: str | None = None,
        quit_wait: float = 20.0,
    ) -> None:
        self.store = store
        self.manager = manager
        self.prompter = prompter
        self.request_exit = request_exit
        self.call_ui = call_ui
        self.run_bg = run_bg
        self.system = system or platform.system()
        self.quit_wait = quit_wait
        self._listeners: list[Task] = []
        self.quitting = False
        manager.subscribe(lambda change: self.call_ui(lambda: self.handle_change(change)))

    # ---- views -------------------------------------------------------------------------------
    def add_listener(self, callback: Task) -> None:
        """`callback` runs (on the UI thread) whenever profile states or the profile list change."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Task) -> None:
        self._listeners = [cb for cb in self._listeners if cb != callback]

    def _changed(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.exception("UI listener failed")

    def profile_items(self) -> list[ProfileItem]:
        items = [
            ProfileItem(p.id, p.name, self.manager.state(p.id), p.mount_point) for p in self.store.profiles()
        ]
        return sorted(items, key=lambda i: i.name.casefold())

    # ---- tray actions ------------------------------------------------------------------------
    def on_profile_clicked(self, profile_id: str) -> None:
        state = self.manager.state(profile_id)
        profile = self.store.get(profile_id)
        if state is State.ACTIVE:
            if self.prompter.confirm(
                "Unmount", f"Unmount “{profile.name}” from {profile.mount_point}?", yes="Unmount", no="Cancel"
            ):
                self.manager.unmount(profile_id)
        elif state is State.INACTIVE:
            self._mount(profile)
        # MOUNTING / UNMOUNTING: menu item is disabled; ignore stray clicks

    def _mount(self, profile: Profile) -> None:
        if fuselib.find_library(self.system) is None:
            self.prompter.error(APP_NAME, f"{_ERROR_TEXT[protocol.FUSE_MISSING]}\n\n{fuselib.hint()}")
            return
        if self.store.password(profile.id) is None:
            self.prompter.error(
                APP_NAME, f"No password is saved for “{profile.name}”. Open Profiles… to set it."
            )
            return
        if self.system != "Windows" and profile.mount_point and not os.path.exists(profile.mount_point):
            try:
                Path(profile.mount_point).mkdir(parents=True)  # WinFsp instead needs a missing folder
            except OSError as e:
                self.prompter.error(APP_NAME, f"Cannot create {profile.mount_point}: {e}")
                return
        try:
            self.manager.mount(profile.id)
        except MountError as e:
            self.prompter.error(f"Cannot mount “{profile.name}”", str(e))
        self._changed()

    def on_quit(self) -> None:
        active = [i for i in self.profile_items() if i.active]
        message = f"Quit {APP_NAME}?"
        if active:
            names = ", ".join(i.name for i in active)
            message = f"Quit {APP_NAME}? These servers will be unmounted: {names}."
        if not self.prompter.confirm("Quit", message, yes="Quit", no="Cancel"):
            return
        if not active:
            self.request_exit()
            return
        self.quitting = True
        self.run_bg(lambda: self._after_unmount(self.manager.unmount_all(wait=self.quit_wait)))

    def _after_unmount(self, still_mounted: list[str]) -> None:
        def ui() -> None:
            if not still_mounted:
                self.request_exit()
                return
            names = ", ".join(still_mounted)
            if self.prompter.confirm(
                "Folders in use",
                f"These folders are still in use (open in a window or a terminal?): {names}.\n\n"
                "Force unmount? Programs using them may lose unsaved work.",
                yes="Force unmount",
                no="Cancel quit",
            ):
                self.run_bg(
                    lambda: self._after_force(self.manager.unmount_all(force=True, wait=self.quit_wait))
                )
            else:
                self.quitting = False
                self._changed()

        self.call_ui(ui)

    def _after_force(self, still_mounted: list[str]) -> None:
        def ui() -> None:
            if still_mounted:
                self.prompter.error(
                    APP_NAME, f"Could not unmount: {', '.join(still_mounted)}. Quitting anyway."
                )
            self.request_exit()

        self.call_ui(ui)

    # ---- manager events ----------------------------------------------------------------------
    def handle_change(self, change: Change) -> None:
        event = change.event or {}
        kind, code = event.get("event"), event.get("code")
        name = self._name(change.profile_id)
        if kind == "error" and code == protocol.BUSY and change.state is State.ACTIVE and not self.quitting:
            if self.prompter.confirm(
                "Folder in use",
                f"“{name}” is in use (open in a window or a terminal?).\n\nForce unmount? "
                "Programs using it may lose unsaved work.",
                yes="Force unmount",
                no="Keep mounted",
            ):
                self.manager.unmount(change.profile_id, force=True)
        elif kind == "error" and code != protocol.BUSY and not self.quitting:
            text = _ERROR_TEXT.get(str(code), "Unexpected error.")
            detail = f"\n\n{change.message}" if change.message else ""
            self.prompter.error(f"Cannot mount “{name}”", f"{text}{detail}")
        elif kind == "imported":
            items = ", ".join(event.get("items", []))
            errors = event.get("compile_errors") or []
            if errors:
                self.prompter.notify(f"{name}: imported with compile errors", f"{items}\n{errors[0][:300]}")
            else:
                self.prompter.notify(f"{name}: imported", items)
        elif kind == "import_failed":
            self.prompter.notify(
                f"{name}: import failed", f"{event.get('file', '')}: {event.get('message', '')}"[:400]
            )
        elif change.state is State.INACTIVE and change.message and not self.quitting:
            self.prompter.error(APP_NAME, change.message)  # worker died unexpectedly
        self._changed()

    def _name(self, profile_id: str) -> str:
        try:
            return self.store.get(profile_id).name
        except KeyError:
            return "?"

    # ---- profiles window ---------------------------------------------------------------------
    def default_mount_point(self, name: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_ ." else "_" for ch in name).strip() or "IRIS"
        return str(Path.home() / APP_NAME / safe)

    def new_profile(self) -> Profile:
        existing = {p.name.casefold() for p in self.store.profiles()}
        n, name = 1, "New server"
        while name.casefold() in existing:
            n += 1
            name = f"New server {n}"
        return Profile(
            name=name, host="localhost", username="_SYSTEM", mount_point=self.default_mount_point(name)
        )

    def is_saved(self, profile_id: str) -> bool:
        return any(p.id == profile_id for p in self.store.profiles())

    def save_profile(self, profile: Profile, password: str | None) -> dict[str, str]:
        """Add or update. Returns field errors (empty dict on success). `password=None` keeps the old one."""
        try:
            if self.is_saved(profile.id):
                self.store.update(profile, password=password)
            else:
                self.store.add(profile, password=password if password is not None else "")
        except ProfileValidationError as e:
            return e.errors
        except ProfileActiveError as e:
            return {"name": str(e)}
        self._changed()
        return {}

    def delete_profile(self, profile_id: str) -> bool:
        profile = self.store.get(profile_id)
        if self.manager.is_active(profile_id):
            self.prompter.error(APP_NAME, f"“{profile.name}” is mounted. Unmount it before deleting it.")
            return False
        if not self.prompter.confirm(
            "Delete profile", f"Delete the profile “{profile.name}”?", yes="Delete", no="Cancel"
        ):
            return False
        self.store.delete(profile_id)
        self._changed()
        return True

    def test_connection(self, profile: Profile, password: str | None) -> tuple[bool, str]:
        """Blocking; call from a background thread. `password=None` uses the saved one."""
        pw = password if password is not None else (self.store.password(profile.id) or "")
        try:
            with AtelierClient(
                profile.base_url, profile.username, pw, verify_tls=profile.verify_tls, timeout=10
            ) as c:
                info = c.server_info(refresh=True)
        except AtelierError as e:
            return False, e.message
        namespaces = ", ".join(info.namespaces)
        return True, f"Connected to {info.version.split(' (')[0]}.\nNamespaces: {namespaces}"
