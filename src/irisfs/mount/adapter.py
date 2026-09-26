"""mfusepy Operations that delegate to VirtualFS. Kept deliberately thin: all logic lives in VirtualFS."""

from __future__ import annotations

import errno
import functools
import logging
import os
from collections.abc import Callable
from types import ModuleType
from typing import Any, TypeVar

from irisfs.vfs.errors import FsError
from irisfs.vfs.vfs import Attr, VirtualFS

log = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])
_QUIET_OPS = {"getattr", "read", "readdir", "statfs"}  # too frequent for the per-operation debug log


def make_operations(fuse: ModuleType, vfs: VirtualFS, *, on_init: Callable[[], None] | None = None) -> Any:
    """Build the Operations instance. `fuse` is the loaded mfusepy module (see fuselib.ensure_loaded)."""
    uid = os.getuid() if hasattr(os, "getuid") else 0
    gid = os.getgid() if hasattr(os, "getgid") else 0

    def translate(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if log.isEnabledFor(logging.DEBUG) and fn.__name__ not in _QUIET_OPS:
                log.debug("op %s%r", fn.__name__, tuple(a for a in args[1:] if not isinstance(a, bytes)))
            try:
                return fn(*args, **kwargs)
            except FsError as e:
                log.debug("op %s -> errno %s", fn.__name__, e.errno)
                raise fuse.FuseOSError(e.errno) from None
            except fuse.FuseOSError:
                raise
            except Exception:
                log.exception("unexpected error in %s%r", fn.__name__, args[1:2])
                raise fuse.FuseOSError(errno.EIO) from None

        return wrapper  # type: ignore[return-value]

    def stat_dict(a: Attr) -> dict[str, Any]:
        ns = int(a.mtime * 1_000_000_000)
        return {
            "st_mode": a.st_mode,
            "st_nlink": 2 if a.is_dir else 1,
            "st_size": a.size,
            "st_mtime": ns,
            "st_ctime": ns,
            "st_atime": ns,
            "st_uid": uid,
            "st_gid": gid,
        }

    class IrisOperations(fuse.Operations):  # type: ignore[name-defined,misc]
        use_ns = True

        def init(self, path: str) -> None:
            if on_init is not None:
                on_init()

        @translate
        def getattr(self, path: str, fh: int | None = None) -> dict[str, Any]:
            return stat_dict(vfs.getattr(path))

        @translate
        def readdir(self, path: str, fh: int) -> list[str]:
            return [".", "..", *vfs.readdir(path)]

        @translate
        def open(self, path: str, flags: int) -> int:
            return vfs.open(path, flags)

        @translate
        def create(self, path: str, mode: int, fi: Any = None) -> int:
            return vfs.create(path, mode)

        @translate
        def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
            return vfs.read(path, size, offset, fh)

        @translate
        def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
            return vfs.write(path, data, offset, fh)

        @translate
        def truncate(self, path: str, length: int, fh: int | None = None) -> int:
            vfs.truncate(path, length, fh)
            return 0

        @translate
        def flush(self, path: str, fh: int) -> int:
            vfs.flush(path, fh)
            return 0

        @translate
        def release(self, path: str, fh: int) -> int:
            vfs.release(path, fh)
            return 0

        @translate
        def fsync(self, path: str, datasync: int, fh: int) -> int:
            return 0

        @translate
        def unlink(self, path: str) -> int:
            vfs.unlink(path)
            return 0

        @translate
        def rename(self, old: str, new: str) -> int:
            vfs.rename(old, new)
            return 0

        @translate
        def mkdir(self, path: str, mode: int) -> int:
            vfs.mkdir(path, mode)
            return 0

        @translate
        def rmdir(self, path: str) -> int:
            vfs.rmdir(path)
            return 0

        @translate
        def chmod(self, path: str, mode: int) -> int:
            vfs.chmod(path, mode)
            return 0

        @translate
        def chown(self, path: str, uid: int, gid: int) -> int:
            vfs.chown(path, uid, gid)
            return 0

        @translate
        def utimens(self, path: str, times: tuple[int, int] | None = None) -> int:
            vfs.utimens(path, times)
            return 0

        @translate
        def setxattr(self, path: str, name: str, value: bytes, options: int, position: int = 0) -> int:
            vfs.setxattr(path, name, value, options)
            return 0

        @translate
        def getxattr(self, path: str, name: str, position: int = 0) -> bytes:
            return vfs.getxattr(path, name)

        @translate
        def listxattr(self, path: str) -> list[str]:
            return vfs.listxattr(path)

        @translate
        def removexattr(self, path: str, name: str) -> int:
            vfs.removexattr(path, name)
            return 0

        @translate
        def statfs(self, path: str) -> dict[str, int]:
            return vfs.statfs()

    return IrisOperations()
