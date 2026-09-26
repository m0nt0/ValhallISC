"""Mount point validation and preparation (plan section 2.7).

POSIX (macOS/Linux): the directory must exist, be empty, writable and not already mounted.
Windows (WinFsp): either a free drive letter, or a path that does NOT exist when mounting. If the chosen
directory exists and is empty we remove it right before mounting and recreate it after unmounting.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from collections.abc import Mapping
from typing import Protocol

_DRIVE_RE = re.compile(r"^([A-Za-z]):\\?$")


class MountPointError(ValueError):
    pass


class FsProbe(Protocol):
    def exists(self, path: str) -> bool: ...
    def is_dir(self, path: str) -> bool: ...
    def is_empty_dir(self, path: str) -> bool: ...
    def is_mount(self, path: str) -> bool: ...
    def writable(self, path: str) -> bool: ...
    def rmdir(self, path: str) -> None: ...
    def mkdir(self, path: str) -> None: ...


class RealProbe:
    def exists(self, path: str) -> bool:
        return os.path.lexists(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def is_empty_dir(self, path: str) -> bool:
        try:
            with os.scandir(path) as it:
                return next(it, None) is None
        except OSError:
            return False

    def is_mount(self, path: str) -> bool:
        from irisfs.mount.unmount import is_mounted  # mount table: safe on dead FUSE mounts

        return is_mounted(path)

    def writable(self, path: str) -> bool:
        return os.access(path, os.W_OK | os.X_OK)

    def rmdir(self, path: str) -> None:
        os.rmdir(path)

    def mkdir(self, path: str) -> None:
        os.mkdir(path)


def normalize(path: str, system: str) -> str:
    if system == "Windows":
        return ntpath.normcase(ntpath.normpath(path.strip()))
    return posixpath.normpath(path.strip())


def _overlaps(a: str, b: str, system: str) -> bool:
    mod = ntpath if system == "Windows" else posixpath
    try:
        common = mod.commonpath([a, b])
    except ValueError:  # different drives on Windows
        return False
    return common in (a, b)


def validate(
    path: str,
    system: str,
    *,
    probe: FsProbe | None = None,
    in_use: Mapping[str, str] | None = None,
) -> None:
    """Raise MountPointError if `path` cannot be used now.

    `in_use` maps the mount paths of active profiles to their names.
    """
    probe = probe or RealProbe()
    raw = path.strip()
    if not raw:
        raise MountPointError("No mount directory configured")
    norm = normalize(raw, system)
    for other, owner in (in_use or {}).items():
        if _overlaps(norm, normalize(other, system), system):
            raise MountPointError(f"{raw} overlaps the mount point of active profile '{owner}' ({other})")

    if system == "Windows":
        drive = _DRIVE_RE.match(raw)
        if drive:
            if probe.exists(f"{drive.group(1).upper()}:\\"):
                raise MountPointError(f"Drive {drive.group(1).upper()}: is already in use")
            return
        drive_part = ntpath.splitdrive(raw)[0]
        if not ntpath.isabs(raw) or len(drive_part) != 2 or drive_part[1] != ":":
            raise MountPointError(f"{raw} is not an absolute local path (C:\\...)")
        parent = ntpath.dirname(norm.rstrip("\\"))
        if not probe.is_dir(parent):
            raise MountPointError(f"Parent folder {parent} does not exist")
        if probe.exists(raw):
            if not probe.is_dir(raw):
                raise MountPointError(f"{raw} exists and is not a folder")
            if probe.is_mount(raw):
                raise MountPointError(f"{raw} is already mounted")
            if not probe.is_empty_dir(raw):
                raise MountPointError(f"{raw} is not empty")
        return

    if not posixpath.isabs(raw):
        raise MountPointError(f"{raw} is not an absolute path")
    # Mount table first: stat on a stale FUSE mount can block.
    if probe.is_mount(raw):
        raise MountPointError(f"{raw} is already a mount point (stale mount? try unmounting it)")
    if not probe.exists(raw):
        raise MountPointError(f"{raw} does not exist")
    if not probe.is_dir(raw):
        raise MountPointError(f"{raw} is not a directory")
    if not probe.is_empty_dir(raw):
        raise MountPointError(f"{raw} is not empty")
    if not probe.writable(raw):
        raise MountPointError(f"{raw} is not writable by the current user")


def ensure_folder(mount_point: str, system: str) -> None:
    """Create what the platform needs before mounting: macOS/Linux mount on an existing empty folder,
    WinFsp needs the folder itself to be missing (only its parent is created); drive letters need nothing."""
    from pathlib import Path

    raw = mount_point.strip()
    if not raw or _DRIVE_RE.match(raw):
        return
    if system == "Windows":
        Path(raw).parent.mkdir(parents=True, exist_ok=True)
    elif not os.path.exists(raw):
        Path(raw).mkdir(parents=True)


def prepare(path: str, system: str, *, probe: FsProbe | None = None) -> bool:
    """Make `path` mountable. Returns True if a directory was removed (restore() must recreate it)."""
    probe = probe or RealProbe()
    if system != "Windows" or _DRIVE_RE.match(path.strip()):
        return False
    if probe.exists(path) and probe.is_dir(path) and probe.is_empty_dir(path):
        probe.rmdir(path)
        return True
    return False


def restore(path: str, system: str, removed: bool, *, probe: FsProbe | None = None) -> None:
    """Undo prepare(): recreate the empty directory removed before mounting (Windows only)."""
    probe = probe or RealProbe()
    if removed and not probe.exists(path):
        probe.mkdir(path)
