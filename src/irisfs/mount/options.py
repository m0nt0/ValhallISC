"""FUSE mount options per platform."""

from __future__ import annotations

import re
from typing import Any

from irisfs.mount.fuselib import FuseLibrary


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:48] or "irisfs"


def mount_options(
    system: str, lib: FuseLibrary | None, *, profile_name: str, read_only: bool
) -> dict[str, Any]:
    opts: dict[str, Any] = {"foreground": True, "fsname": f"irisfs-{_safe(profile_name)}"}
    if read_only:
        opts["ro"] = True
    if system == "Linux":
        # Short kernel caches so changes made in IRIS show up quickly; our own TTL cache does the rest.
        opts.update(attr_timeout=1.0, entry_timeout=1.0)
        if lib is not None and lib.kind == "libfuse3":
            opts["auto_unmount"] = True  # kernel-side cleanup if the worker dies
    elif system == "Darwin":
        opts["volname"] = profile_name[:60] or "IRIS"
        if lib is not None and lib.kind.startswith("macFUSE"):
            opts.update(noappledouble=True, noapplexattr=True)
    elif system == "Windows":
        # WinFsp-FUSE: map file ownership to the current user. [VERIFY on Windows]
        opts.update(uid=-1, gid=-1, FileSystemName="IRISFS", volname=profile_name[:32] or "IRIS")
    return opts
