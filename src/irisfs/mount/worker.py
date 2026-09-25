"""Mount worker: one process per mounted profile (plan sections 2.5 and 2.6).

Reads {"cmd":"start",...} from stdin, checks credentials, mounts with FUSE in the foreground and reports
events as JSON lines on stdout. A later {"cmd":"stop"} (or stdin EOF, i.e. the parent died) unmounts.
The FUSE loop runs in the main thread because libfuse installs its own signal handlers there.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any

from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AtelierError, AuthError, ConnectionFailed
from irisfs.config import mountpoint
from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.mount import fuselib, protocol
from irisfs.mount.options import mount_options
from irisfs.mount.unmount import is_mounted, unmount
from irisfs.vfs.vfs import Options, VirtualFS

log = logging.getLogger(__name__)
Emit = Callable[[dict[str, Any]], None]

# exit codes
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_AUTH = 3
EXIT_MOUNT = 4


@dataclass
class WorkerConfig:
    profile: Profile
    password: str
    tree_ttl: float = 10.0

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> WorkerConfig:
        if message.get("cmd") != "start" or not isinstance(message.get("profile"), dict):
            raise ValueError("expected a start command with a profile")
        profile = Profile.from_dict(message["profile"])
        return cls(
            profile=profile,
            password=str(message.get("password", "")),
            tree_ttl=float(message.get("tree_ttl", 10.0)),
        )


class Worker:
    def __init__(self, config: WorkerConfig, emit: Emit, *, system: str | None = None) -> None:
        self.config = config
        self.emit = emit
        self.system = system or platform.system()
        self.mount_path = config.profile.mount_point
        self._stopping = threading.Event()
        self._removed_dir = False

    def run(self) -> int:
        p = self.config.profile
        try:
            p.check(self.system)
        except ProfileValidationError as e:
            return self._fail(protocol.BAD_CONFIG, str(e), EXIT_CONFIG)

        try:
            fuse = fuselib.ensure_loaded()
        except fuselib.FuseNotFoundError as e:
            return self._fail(protocol.FUSE_MISSING, str(e), EXIT_MOUNT)

        client = AtelierClient(p.base_url, p.username, self.config.password, verify_tls=p.verify_tls)
        try:
            info = client.server_info(refresh=True)
            log.info("connected to %s (%s, API v%s)", p.base_url, info.version, info.api)
        except AuthError as e:
            client.close()
            return self._fail(protocol.AUTH_FAILED, e.message, EXIT_AUTH)
        except ConnectionFailed as e:
            client.close()
            return self._fail(protocol.UNREACHABLE, e.message, EXIT_AUTH)
        except AtelierError as e:
            client.close()
            return self._fail(protocol.SERVER_ERROR, e.message, EXIT_AUTH)

        try:
            mountpoint.validate(self.mount_path, self.system)
            self._removed_dir = mountpoint.prepare(self.mount_path, self.system)
        except (mountpoint.MountPointError, OSError) as e:
            client.close()
            return self._fail(protocol.MOUNTPOINT_INVALID, str(e), EXIT_MOUNT)

        vfs = VirtualFS(
            client,
            Options(
                read_only=p.read_only,
                show_system=p.show_system,
                compile_on_import=p.compile_on_import,
                compile_flags=p.compile_flags,
                tree_ttl=self.config.tree_ttl,
                case_insensitive=self.system in ("Darwin", "Windows"),
                no_index_marker=self.system == "Darwin",
            ),
            on_event=self.emit,
        )
        from irisfs.mount.adapter import make_operations

        mounted = threading.Event()

        def announce() -> None:
            # FUSE's init callback can run before the mount is visible to other processes (macOS), so
            # report "mounted" only once the mount table shows it; readers then never hit the empty folder.
            deadline = time.monotonic() + 15
            while not is_mounted(self.mount_path) and time.monotonic() < deadline:
                time.sleep(0.05)
            if not is_mounted(self.mount_path):
                log.warning("mount of %s not visible in the mount table after 15 s", self.mount_path)
            log.info("mounted %s at %s", p.name, self.mount_path)
            self.emit({"event": "mounted", "mountpoint": self.mount_path})

        def on_init() -> None:
            mounted.set()
            threading.Thread(target=announce, name="announce", daemon=True).start()

        ops = make_operations(fuse, vfs, on_init=on_init)
        opts = self.fuse_options()
        code = EXIT_OK
        try:
            fuse.FUSE(ops, self.mount_path, **opts)
        except RuntimeError as e:
            # libfuse returns non-zero when its loop ends through a signal; that's a normal shutdown
            # once we were mounted, but a real failure if we never got there.
            if not mounted.is_set():
                code = self._fail(protocol.MOUNT_FAILED, f"mount failed ({e})", EXIT_MOUNT)
        finally:
            vfs.close()
            client.close()
            mountpoint.restore(self.mount_path, self.system, self._removed_dir)
        if mounted.is_set():
            log.info("unmounted %s", self.mount_path)
            self.emit({"event": "unmounted", "mountpoint": self.mount_path})
        return code

    def fuse_options(self) -> dict[str, Any]:
        p = self.config.profile
        return mount_options(
            self.system,
            fuselib.find_library(self.system),
            profile_name=p.name,
            read_only=p.read_only,
            mount_point=self.mount_path,
        )

    def stop(self, *, force: bool = False) -> None:
        """Called from the stdin thread. Unmounting makes the FUSE loop in the main thread return."""
        self._stopping.set()
        if self.system == "Windows":
            # WinFsp removes the mount when the process ends. [VERIFY on Windows]
            mountpoint.restore(self.mount_path, self.system, self._removed_dir)
            self.emit({"event": "unmounted", "mountpoint": self.mount_path})
            os._exit(EXIT_OK)
        result = unmount(self.mount_path, self.system, force=force)
        if not result.ok:
            log.warning("unmount of %s failed: %s", self.mount_path, result.message)
            code = protocol.BUSY if result.busy else protocol.MOUNT_FAILED
            self.emit({"event": "error", "code": code, "message": result.message or "unmount failed"})

    def _fail(self, code: str, message: str, exit_code: int) -> int:
        log.error("%s: %s", code, message)
        self.emit({"event": "error", "code": code, "message": message})
        return exit_code


def _command_loop(worker: Worker, stream: IO[str]) -> None:
    for line in stream:
        message = protocol.decode(line)
        if message is None:
            continue
        if message.get("cmd") == "stop":
            worker.stop(force=bool(message.get("force")))
        elif message.get("cmd") == "ping":
            worker.emit({"event": "pong"})
    # EOF: the parent is gone - do not leave an orphaned mount behind
    if not worker._stopping.is_set():
        log.info("stdin closed; unmounting")
        worker.stop(force=True)


def events_stream() -> IO[str]:
    """Reserve the real stdout for protocol events and point fd 1 at stderr, so anything libfuse or a
    library prints cannot corrupt the event stream."""
    sys.stdout.flush()
    events_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(events_fd, "w", encoding="utf-8", buffering=1)


def main() -> int:
    from irisfs import log as logsetup

    out = events_stream()
    emit = protocol.EventWriter(out)
    first = sys.stdin.readline()
    message = protocol.decode(first)
    try:
        if message is None:
            raise ValueError("no start command on stdin")
        config = WorkerConfig.from_message(message)
    except (ValueError, TypeError) as e:
        emit({"event": "error", "code": protocol.BAD_CONFIG, "message": str(e)})
        return EXIT_CONFIG
    logsetup.setup(f"worker-{config.profile.id[:8]}", verbose=bool(message.get("verbose")))
    worker = Worker(config, emit)
    threading.Thread(target=_command_loop, args=(worker, sys.stdin), name="stdin", daemon=True).start()
    return worker.run()
