"""Unmount commands per platform."""

from __future__ import annotations

import os
import re
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


_MOUNT_LINE = re.compile(r"^(?P<source>\S+) on (?P<path>.+?) (?:\(|type )")


def canonical(path: str) -> str:
    """Absolute path with symlinks resolved in the *parent* only. Never touches the path itself: stat on a
    FUSE mount whose worker died blocks (macFUSE daemon timeout) or fails."""
    head, tail = os.path.split(os.path.abspath(path).rstrip(os.sep) or os.sep)
    return os.path.join(os.path.realpath(head), tail) if tail else head


def mount_table() -> dict[str, str]:
    """{mount point: source} from `mount` (macOS, Linux); empty elsewhere or on failure."""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    table = {}
    for line in out.splitlines():
        match = _MOUNT_LINE.match(line)
        if match:
            table[canonical(match.group("path"))] = match.group("source")
    return table


def is_mounted(path: str) -> bool:
    if os.name == "nt":
        try:
            return os.path.ismount(path)
        except OSError:
            return False
    return canonical(path) in mount_table()
