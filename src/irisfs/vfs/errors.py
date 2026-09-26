"""Filesystem errors carry an errno; the FUSE adapter turns them into FuseOSError."""

from __future__ import annotations

import os


class FsError(OSError):
    def __init__(self, errno_: int, detail: str = "") -> None:
        super().__init__(errno_, detail or os.strerror(errno_))
        self.detail = detail
