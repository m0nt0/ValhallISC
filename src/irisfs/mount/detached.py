"""Background ("detached") mounts started from the CLI, and disconnecting any registered mount."""

from __future__ import annotations

import contextlib
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from irisfs.config.profile import Profile
from irisfs.mount import protocol, registry
from irisfs.mount.unmount import UnmountResult, is_mounted, unmount

log = logging.getLogger(__name__)


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "worker"]
    return [sys.executable, "-m", "irisfs", "worker"]


def connect(
    profile: Profile,
    password: str,
    registry_dir: Path,
    *,
    wait: float = 60.0,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Start a worker that outlives this process. Returns the first decisive event
    ({"event": "mounted"} or {"event": "error", ...}); a timeout is reported as an error event."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
        )
        kwargs["creationflags"] = flags | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    else:
        kwargs["start_new_session"] = True  # not killed with the terminal / parent
    proc = subprocess.Popen(
        command or worker_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        **kwargs,
    )
    assert proc.stdin is not None and proc.stdout is not None
    start = {
        "cmd": "start",
        "profile": profile.to_dict(),
        "password": password,
        "detach": True,
        "owner": "cli",
        "registry_dir": str(registry_dir),
    }
    proc.stdin.write(protocol.encode(start))
    proc.stdin.flush()

    result: dict[str, Any] = {}
    done = threading.Event()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            event = protocol.decode(line)
            if event and event.get("event") in ("mounted", "error"):
                result.update(event)
                done.set()
                return
        result.update({"event": "error", "code": protocol.MOUNT_FAILED, "message": "worker exited"})
        done.set()

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(wait):
        proc.kill()
        return {"event": "error", "code": "TIMEOUT", "message": f"not mounted within {wait:.0f} s"}
    if result.get("event") != "mounted":
        proc.wait(10)
    # Detach: close our ends; the worker keeps running (detach=True ignores stdin EOF, events go nowhere).
    for stream in (proc.stdin, proc.stdout):
        with contextlib.suppress(OSError):
            stream.close()
    result.setdefault("pid", proc.pid)
    return result


def disconnect(
    entry: registry.MountEntry, *, force: bool = False, system: str | None = None, wait: float = 15.0
) -> UnmountResult:
    """Unmount a registered mount (started by the CLI or the tray) and wait for its worker to exit."""
    system = system or platform.system()
    if system == "Windows":
        # WinFsp removes the mount when the process ends; /T also ends the onefile bootloader's child.
        r = subprocess.run(["taskkill", "/PID", str(entry.pid), "/T", "/F"], capture_output=True, text=True)
        result = UnmountResult(r.returncode == 0, False, (r.stdout or r.stderr).strip())
    else:
        result = unmount(entry.mountpoint, system, force=force)
    if not result.ok:
        return result
    deadline = time.monotonic() + wait
    while registry.pid_alive(entry.pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if registry.pid_alive(entry.pid):
        log.warning("worker %s still running after unmount; terminating", entry.pid)
        with contextlib.suppress(OSError):
            os.kill(entry.pid, 15)
    if system != "Windows" and is_mounted(entry.mountpoint):
        return UnmountResult(False, False, f"{entry.mountpoint} is still mounted")
    return result
