"""Unmount commands per platform."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class UnmountResult:
    ok: bool
    busy: bool
    message: str


def unmount_command(path: str, system: str, *, force: bool = False) -> list[str] | None:
    if system == "Linux":
        tool = shutil.which("fusermount3") or shutil.which("fusermount") or "fusermount3"
        return [tool, "-uz" if force else "-u", path]
    if system == "Darwin":
        return ["diskutil", "unmount", "force", path] if force else ["umount", path]
    return None  # Windows: the worker process ends and WinFsp removes the mount


def unmount(path: str, system: str, *, force: bool = False, timeout: float = 15.0) -> UnmountResult:
    cmd = unmount_command(path, system, force=force)
    if cmd is None:
        return UnmountResult(False, False, "no unmount command on this platform")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return UnmountResult(False, False, str(e))
    message = (r.stderr or r.stdout).strip()
    if r.returncode == 0:
        return UnmountResult(True, False, message)
    busy = "busy" in message.lower()
    return UnmountResult(False, busy, message or f"{cmd[0]} exited with {r.returncode}")


def is_mounted(path: str) -> bool:
    try:
        return os.path.ismount(path)
    except OSError:
        return False
