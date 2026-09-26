"""Registry of live mounts, shared by the tray app and the CLI.

Every worker writes <config dir>/mounts/<profile id>.json once it is mounted and removes it when it exits.
Entries whose worker process is gone are ignored (and cleaned up) by readers, so a crash cannot leave a
profile looking "connected" forever.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MountEntry:
    profile_id: str
    name: str
    mountpoint: str
    pid: int
    owner: str  # "tray" | "cli"
    started: float


def registry_dir(config_dir: Path) -> Path:
    return config_dir / "mounts"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information, still_active = 0x1000, 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def write(directory: Path, entry: MountEntry) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{entry.profile_id}.tmp"
    tmp.write_text(json.dumps(asdict(entry)), encoding="utf-8")
    os.replace(tmp, directory / f"{entry.profile_id}.json")


def remove(directory: Path, profile_id: str, pid: int | None = None) -> None:
    """Remove the entry for `profile_id` (only if it still belongs to `pid`, when given)."""
    path = directory / f"{profile_id}.json"
    if pid is not None:
        current = _read(path)
        if current is not None and current.pid != pid:
            return
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _read(path: Path) -> MountEntry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return MountEntry(**data)
    except (OSError, ValueError, TypeError):
        return None


def entries(directory: Path) -> dict[str, MountEntry]:
    """Live entries by profile id; stale ones (dead worker) are deleted."""
    live: dict[str, MountEntry] = {}
    if not directory.is_dir():
        return live
    for path in directory.glob("*.json"):
        entry = _read(path)
        if entry is None or not pid_alive(entry.pid):
            log.info("removing stale mount registry entry %s", path.name)
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        live[entry.profile_id] = entry
    return live


def get(directory: Path, profile_id: str) -> MountEntry | None:
    return entries(directory).get(profile_id)


def new_entry(profile_id: str, name: str, mountpoint: str, owner: str) -> MountEntry:
    return MountEntry(profile_id, name, mountpoint, os.getpid(), owner, time.time())
