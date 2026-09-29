"""Application logic behind the tray icon and the Profiles window (no wx imports: unit-testable).

Threading: every public method is called on the UI thread. Slow work goes through `run_bg`, and results
come back through `call_ui` (wx.CallAfter in the app, immediate calls in tests).
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from irisfs import APP_NAME
from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AtelierError, ConnectionFailed
from irisfs.config import mountpoint
from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.config.store import ProfileActiveError, ProfileStore
from irisfs.gui.prompter import Prompter
from irisfs.mount import fuse_help, protocol
from irisfs.mount.manager import Change, MountError, MountManager, State

log = logging.getLogger(__name__)
Task = Callable[[], None]

_ERROR_TEXT = {
    protocol.AUTH_FAILED: "The server rejected the user name or password.",
    protocol.UNREACHABLE: "The server could not be reached. Check address and port and that IRIS is running. "
    "IRIS 2023.2 and later have no built-in web server: use the web server's port (80, or 443 with HTTPS).",
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
    external: bool = False  # mounted by `valhallisc connect`, not by this app
    host: str = ""
    port: int = 0
    read_only: bool = False

    @property
    def active(self) -> bool:
        return self.state is not State.INACTIVE

    @property
    def mounted(self) -> bool:
        return self.state is State.ACTIVE

    @property
    def status(self) -> str:
        """Short state word(s) used in the menu (design review: words instead of a bare ✓)."""
        if self.state is State.MOUNTING:
            return "Mounting…"
        if self.state is State.UNMOUNTING:
            return "Unmounting…"
        if self.state is State.ACTIVE:
            return "Mounted from CLI" if self.external else "Mounted"
        return ""

    @property
    def subtitle(self) -> str:
        """Where it is mounted, or which server it points at (the tray submenu's first line)."""
        if self.state is State.ACTIVE:
            extra = " · read-only" if self.read_only else ""
            return f"{self.status} · {short_path(self.mount_point)}{extra}"
        if self.state in (State.MOUNTING, State.UNMOUNTING):
            return self.status
        return f"{self.host}:{self.port} · not mounted" if self.host else "not mounted"

    @property
    def row_subtitle(self) -> str:
        """Second line of a Profiles list row, kept to one line (design review 2 #3)."""
        if self.state is State.ACTIVE:
            return self.status + (" · read-only" if self.read_only else "")
        return self.subtitle

    @property
    def label(self) -> str:
        if self.state is State.MOUNTING:
            return f"{self.name} (mounting…)"
        if self.state is State.UNMOUNTING:
            return f"{self.name} (unmounting…)"
        if self.external:
            return f"{self.name} (mounted from CLI)"
        return self.name

    @property
    def enabled(self) -> bool:
        return self.state in (State.ACTIVE, State.INACTIVE)


def short_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


def open_in_file_manager(path: str) -> None:
    """Finder / Explorer / the desktop's file manager."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined,unused-ignore]
    else:
        subprocess.Popen(["xdg-open", path])


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
        self._opener: Callable[[str], None] | None = None  # tests replace the file-manager launcher
        self.fuse_check: Callable[[], fuse_help.Advice | None] = self.default_fuse_check
        self.fuse_missing_handler: Callable[[fuse_help.Advice], None] | None = None
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
            ProfileItem(
                p.id,
                p.name,
                self.manager.state(p.id),
                p.mount_point,
                self.manager.external(p.id) is not None,
                p.host,
                p.port,
                p.read_only,
            )
            for p in self.store.profiles()
        ]
        return sorted(items, key=lambda i: i.name.casefold())

    def summary(self) -> str:
        items = self.profile_items()
        mounted = sum(1 for i in items if i.mounted)
        return f"{mounted} of {len(items)} mounted" if items else "no servers yet"

    # ---- tray actions ------------------------------------------------------------------------
    def open_folder(self, profile_id: str) -> None:
        profile = self.store.get(profile_id)
        try:
            (self._opener or open_in_file_manager)(profile.mount_point)
        except OSError as e:
            self.prompter.error(APP_NAME, f"Cannot open {profile.mount_point}: {e}")

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

    def mount(self, profile_id: str) -> None:
        """Mount a saved profile (the Profiles window's Mount button)."""
        if self.manager.state(profile_id) is State.INACTIVE:
            self._mount(self.store.get(profile_id))

    def default_fuse_check(self) -> fuse_help.Advice | None:
        # /dev/fuse can only be probed for the platform we actually run on (tests simulate others)
        native = self.system == platform.system()
        return fuse_help.advice(self.system, dev_fuse_exists=None if native else True)

    def _mount(self, profile: Profile) -> None:
        advice = self.fuse_check()
        if advice is not None:
            if self.fuse_missing_handler is not None:
                self.fuse_missing_handler(advice)  # the app shows the install dialog
            else:
                self.prompter.error(APP_NAME, advice.as_text())
            return
        if self.store.password(profile.id) is None:
            self.prompter.error(
                APP_NAME, f"No password is saved for “{profile.name}”. Open Profiles… to set it."
            )
            return
        try:
            self._prepare_folder(profile.mount_point)
        except OSError as e:
            self.prompter.error(APP_NAME, f"Cannot create the folder for {profile.mount_point}: {e}")
            return
        try:
            self.manager.mount(profile.id)
        except MountError as e:
            self.prompter.error(f"Cannot mount “{profile.name}”", str(e))
        self._changed()

    def _prepare_folder(self, mount_point: str) -> None:
        mountpoint.ensure_folder(mount_point, self.system)

    def on_quit(self) -> None:
        active = [i for i in self.profile_items() if i.active and not i.external]  # CLI mounts stay
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

    def test_connection(self, profile: Profile, password: str | None) -> TestResult:
        """Blocking; call from a background thread. `password=None` uses the saved one."""
        pw = password if password is not None else (self.store.password(profile.id) or "")
        try:
            with AtelierClient(
                profile.base_url, profile.username, pw, verify_tls=profile.verify_tls, timeout=10
            ) as c:
                info = c.server_info(refresh=True)
        except ConnectionFailed as e:
            return TestResult(False, e.message, port_hint(profile))
        except AtelierError as e:
            return TestResult(False, e.message)
        namespaces = ", ".join(info.namespaces)
        return TestResult(True, f"Connected to {info.version.split(' (')[0]}.\nNamespaces: {namespaces}")


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    message: str
    hint: str = ""  # shown under the Port field (design review 2 #9)


def port_hint(profile: Profile) -> str:
    """Why an unreachable server may be on another port: since IRIS 2023.2 new installations have no
    private web server on 52773; the Atelier API is then behind the web server (80/443, maybe a prefix)."""
    web_port = 443 if profile.https else 80
    if profile.port == web_port:
        return "Check the URL prefix (Options) if the web server serves IRIS under a path, e.g. /iris."
    return (
        f"IRIS 2023.2 and later have no built-in web server: try port {web_port} "
        "(the web server's) and, if needed, a URL prefix in Options."
    )
