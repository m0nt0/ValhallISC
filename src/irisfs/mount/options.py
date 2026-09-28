"""FUSE mount options per platform."""

from __future__ import annotations

import os
import re
from typing import Any

from irisfs.mount.fuselib import FuseLibrary


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:48] or "irisfs"


def mount_options(
    system: str, lib: FuseLibrary | None, *, profile_name: str, read_only: bool, mount_point: str = ""
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
        # Finder shows the volume name in place of the mount folder's name, so use the folder's name.
        folder = os.path.basename(os.path.normpath(mount_point)) if mount_point else ""
        opts["volname"] = (folder or profile_name)[:60] or "IRIS"
        if lib is not None and lib.kind.startswith("macFUSE"):
            # "local" makes Finder list the volume in the sidebar (non-local volumes are hidden there).
            # Spotlight is kept out by the virtual /.metadata_never_index file (see VirtualFS).
            # Not "noapplexattr": it makes the kernel refuse com.apple.* xattrs with EPERM, and Finder
            # aborts copies with a permission error. VirtualFS accepts xattrs as no-ops instead.
            opts.update(noappledouble=True, local=True)
        elif lib is not None and lib.kind == "FUSE-T":
            opts["location"] = "ValhallISC"  # Finder groups FUSE-T volumes under this name, not "localhost"
            # FUSE-T serves the volume over NFS; the macOS NFS client's attribute cache kept "not found"
            # answers given during a server outage indefinitely. No measurable cost (our own caches answer).
            opts["noattrcache"] = True
            # NFSv4 named attributes: without them the NFS client never asks for extended attributes, and
            # the Finder tags that show the source control state (ADR-017) stay invisible.
            opts["namedattr"] = True
    elif system == "Windows":
        # WinFsp-FUSE: map file ownership to the current user. [VERIFY on Windows]
        opts.update(uid=-1, gid=-1, FileSystemName="IRISFS", volname=profile_name[:32] or "IRIS")
    # Power users / debugging: extra options, e.g. VALHALLISC_FUSE_OPTIONS="noattrcache,rwsize=65536"
    for item in filter(None, (x.strip() for x in os.environ.get("VALHALLISC_FUSE_OPTIONS", "").split(","))):
        key, sep, value = item.partition("=")
        opts[key] = value if sep else True
    return opts
