"""MountManager: starts/stops one worker process per mounted profile (plan section 2.6).

    INACTIVE --mount()--> MOUNTING --"mounted"--> ACTIVE --unmount()--> UNMOUNTING --exit--> INACTIVE
         ^                   | error / timeout                  | worker died unexpectedly
         +-------------------+----------------------------------+--> INACTIVE (+ notification, cleanup)

All methods are non-blocking except unmount_all(wait=...) (used on Quit). Subscribers are called from
background threads; the GUI must marshal to its own thread (wx.CallAfter).
"""

from __future__ import annotations

import enum
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from irisfs.config import mountpoint
from irisfs.config.store import ProfileStore
from irisfs.mount import protocol, registry
from irisfs.mount.unmount import UnmountResult, canonical, is_mounted, mount_table, unmount

log = logging.getLogger(__name__)


class State(enum.Enum):
    INACTIVE = "inactive"
    MOUNTING = "mounting"
    ACTIVE = "active"
    UNMOUNTING = "unmounting"


class MountError(RuntimeError):
    """A mount request that can be rejected before starting a worker."""


@dataclass(frozen=True)
class Change:
    """Published to subscribers. `event` is the worker event that caused it (if any)."""

    profile_id: str
    state: State
    event: dict[str, Any] | None = None
    message: str | None = None


Subscriber = Callable[[Change], None]
UnmountFn = Callable[..., UnmountResult]


def default_worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "worker"]
    return [sys.executable, "-m", "irisfs", "worker"]


@dataclass
class _Mount:
    profile_id: str
    name: str
    path: str
    proc: subprocess.Popen[str]
    state: State = State.MOUNTING
    busy: bool = False
    force_requested: bool = False
    exited: threading.Event = field(default_factory=threading.Event)


class MountManager:
    def __init__(
        self,
        store: ProfileStore,
        *,
        worker_command: list[str] | None = None,
        system: str | None = None,
        mount_timeout: float = 30.0,
        stop_timeout: float = 5.0,
        unmount_fn: UnmountFn = unmount,
        env: dict[str, str] | None = None,
        registry_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.registry_dir = registry_dir or registry.registry_dir(store.path.parent)
        self.worker_command = worker_command or default_worker_command()
        self.system = system or platform.system()
        self.mount_timeout = mount_timeout
        self.stop_timeout = stop_timeout
        self._unmount = unmount_fn
        self._env = env
        self._lock = threading.RLock()
        self._mounts: dict[str, _Mount] = {}
        self._subscribers: list[Subscriber] = []
        self._external_stopping: set[str] = set()
        store.is_active = self.is_active

    # ---- queries -----------------------------------------------------------------------------
    def state(self, profile_id: str) -> State:
        with self._lock:
            m = self._mounts.get(profile_id)
            if m is not None:
                return m.state
            if profile_id in self._external_stopping:
                return State.UNMOUNTING
        return State.ACTIVE if self.external(profile_id) else State.INACTIVE

    def external(self, profile_id: str) -> registry.MountEntry | None:
        """A live mount of this profile that this manager did not start (e.g. `valhallisc connect`)."""
        with self._lock:
            if profile_id in self._mounts:
                return None
        return registry.get(self.registry_dir, profile_id)

    def is_active(self, profile_id: str) -> bool:
        """True while a profile is mounted or changing state (it must not be edited or deleted)."""
        return self.state(profile_id) is not State.INACTIVE

    def active_paths(self) -> dict[str, str]:
        paths = {e.mountpoint: e.name for e in registry.entries(self.registry_dir).values()}
        with self._lock:
            paths.update({m.path: m.name for m in self._mounts.values()})
        return paths

    def subscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def _publish(self, change: Change) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for cb in subscribers:
            try:
                cb(change)
            except Exception:
                log.exception("subscriber failed")

    # ---- mount -------------------------------------------------------------------------------
    def mount(self, profile_id: str) -> None:
        profile = self.store.get(profile_id)
        with self._lock:
            if profile_id in self._mounts:
                raise MountError(f"'{profile.name}' is already {self._mounts[profile_id].state.value}")
            if self.external(profile_id):
                raise MountError(f"'{profile.name}' is already connected (from the command line)")
            try:
                mountpoint.validate(profile.mount_point, self.system, in_use=self.active_paths())
            except mountpoint.MountPointError as e:
                raise MountError(str(e)) from e
            password = self.store.password(profile_id) or ""
            env = dict(self._env if self._env is not None else os.environ)
            proc = subprocess.Popen(
                self.worker_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # the worker logs to its own file
                text=True,
                encoding="utf-8",
                env=env,
            )
            m = _Mount(profile_id, profile.name, profile.mount_point, proc)
            self._mounts[profile_id] = m
        start = {
            "cmd": "start",
            "profile": profile.to_dict(),
            "password": password,
            "owner": "tray",
            "registry_dir": str(self.registry_dir),
        }
        self._send(m, start)
        threading.Thread(
            target=self._read_events, args=(m,), name=f"events-{profile.name}", daemon=True
        ).start()
        threading.Thread(
            target=self._mount_timeout, args=(m,), name=f"timeout-{profile.name}", daemon=True
        ).start()
        log.info("mounting %s at %s (pid %s)", profile.name, profile.mount_point, proc.pid)
        self._publish(Change(profile_id, State.MOUNTING))

    def _send(self, m: _Mount, message: dict[str, Any]) -> bool:
        stdin: IO[str] | None = m.proc.stdin
        if stdin is None:
            return False
        try:
            stdin.write(protocol.encode(message))
            stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _read_events(self, m: _Mount) -> None:
        assert m.proc.stdout is not None
        for line in m.proc.stdout:
            event = protocol.decode(line)
            if event is not None:
                self._on_event(m, event)
        m.proc.wait()
        m.exited.set()
        self._on_exit(m)

    def _on_event(self, m: _Mount, event: dict[str, Any]) -> None:
        kind = event.get("event")
        if kind == "mounted":
            with self._lock:
                m.state = State.ACTIVE
            log.info("%s mounted", m.name)
            self._publish(Change(m.profile_id, State.ACTIVE, event))
        elif kind == "error":
            code = event.get("code")
            with self._lock:
                state = m.state
                if code == protocol.BUSY:
                    m.busy = True
            if code == protocol.BUSY and state is State.UNMOUNTING and not m.force_requested:
                with self._lock:
                    m.state = State.ACTIVE  # still mounted; the user may retry with force
                self._publish(Change(m.profile_id, State.ACTIVE, event, "The folder is in use"))
            else:
                self._publish(Change(m.profile_id, state, event, str(event.get("message", ""))))
        elif kind in ("imported", "import_failed"):
            self._publish(Change(m.profile_id, self.state(m.profile_id), event))

    def _on_exit(self, m: _Mount) -> None:
        with self._lock:
            previous = m.state
            if self._mounts.get(m.profile_id) is m:
                del self._mounts[m.profile_id]
        code = m.proc.returncode
        message = None
        if previous is State.ACTIVE and code == 0:
            log.info("%s was unmounted outside the app (eject, `valhallisc disconnect`, ...)", m.name)
        elif previous is State.ACTIVE:
            message = f"The connection '{m.name}' stopped unexpectedly (exit code {code})"
            log.warning(message)
            self._cleanup_path(m.path)
        elif previous is State.UNMOUNTING and is_mounted(m.path):
            self._cleanup_path(m.path)
        log.info("%s worker exited (%s), was %s", m.name, code, previous.value)
        self._publish(Change(m.profile_id, State.INACTIVE, None, message))

    def _mount_timeout(self, m: _Mount) -> None:
        if m.exited.wait(self.mount_timeout):
            return
        with self._lock:
            timed_out = m.state is State.MOUNTING
        if timed_out:
            log.error("%s did not mount within %ss", m.name, self.mount_timeout)
            self._publish(
                Change(
                    m.profile_id, State.MOUNTING, {"event": "error", "code": "TIMEOUT"}, "Mounting timed out"
                )
            )
            self._kill(m)

    # ---- unmount -----------------------------------------------------------------------------
    def unmount(self, profile_id: str, *, force: bool = False) -> None:
        """Ask the worker to unmount. Non-blocking; progress is published to subscribers."""
        with self._lock:
            m = self._mounts.get(profile_id)
            if m is None:
                entry = self.external(profile_id)
                if entry is not None:
                    self._external_stopping.add(profile_id)
                    threading.Thread(target=self._stop_external, args=(entry, force), daemon=True).start()
                    self._publish(Change(profile_id, State.UNMOUNTING))
                return
            if m.state is State.MOUNTING:
                force = True  # abort a mount in progress
            m.state = State.UNMOUNTING
            m.busy = False
            m.force_requested = force
        self._publish(Change(profile_id, State.UNMOUNTING))
        threading.Thread(target=self._stop, args=(m, force), name=f"stop-{m.name}", daemon=True).start()

    def _stop(self, m: _Mount, force: bool) -> None:
        self._send(m, {"cmd": "stop", "force": force})
        if m.exited.wait(self.stop_timeout):
            return
        with self._lock:
            busy = m.busy
        if busy and not force:
            return  # BUSY was reported; the state went back to ACTIVE
        # Escalate: OS-level unmount, then kill.
        log.warning("%s did not stop in %ss; forcing", m.name, self.stop_timeout)
        self._unmount(m.path, self.system, force=True)
        if m.exited.wait(self.stop_timeout):
            return
        self._kill(m)

    def _stop_external(self, entry: registry.MountEntry, force: bool) -> None:
        from irisfs.mount.detached import disconnect

        result = disconnect(entry, force=force, system=self.system)
        with self._lock:
            self._external_stopping.discard(entry.profile_id)
        if result.ok:
            self._publish(Change(entry.profile_id, State.INACTIVE))
        else:
            code = protocol.BUSY if result.busy else protocol.MOUNT_FAILED
            event = {"event": "error", "code": code, "message": result.message}
            self._publish(Change(entry.profile_id, State.ACTIVE, event, result.message))

    def _kill(self, m: _Mount) -> None:
        if m.proc.poll() is None:
            m.proc.kill()
        m.exited.wait(self.stop_timeout)
        self._cleanup_path(m.path)

    def _cleanup_path(self, path: str) -> None:
        if is_mounted(path):
            result = self._unmount(path, self.system, force=True)
            log.info("cleanup unmount of %s: %s", path, "ok" if result.ok else result.message)

    def unmount_all(self, *, force: bool = False, wait: float | None = None) -> list[str]:
        """Stop every worker in parallel. With `wait`, block until they exit; returns names still mounted."""
        with self._lock:
            mounts = list(self._mounts.values())
        for m in mounts:
            self.unmount(m.profile_id, force=force)
        if wait is None:
            return []
        deadline = time.monotonic() + wait
        for m in mounts:
            m.exited.wait(max(0.0, deadline - time.monotonic()))
        return [m.name for m in mounts if not m.exited.is_set()]

    # ---- stale mounts ------------------------------------------------------------------------
    def cleanup_stale(self) -> list[str]:
        """Unmount irisfs mounts left behind at profile mount points (e.g. after a crash)."""
        ours = set(irisfs_mounts())
        live = {canonical(e.mountpoint) for e in registry.entries(self.registry_dir).values()}
        ours -= live  # mounts whose worker is alive (CLI `connect`) are not stale
        cleaned = []
        for p in self.store.profiles():
            path = canonical(p.mount_point) if p.mount_point else ""
            if self.state(p.id) is State.INACTIVE and path in ours:
                result = self._unmount(path, self.system, force=True)
                log.info("stale mount %s: %s", path, "removed" if result.ok else result.message)
                if result.ok:
                    cleaned.append(path)
        return cleaned


def irisfs_mounts() -> list[str]:
    """Mount points of all irisfs filesystems currently mounted (fsname irisfs-*), from the mount table."""
    return [path for path, source in mount_table().items() if source.startswith("irisfs-")]
