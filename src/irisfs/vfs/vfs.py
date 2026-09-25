"""VirtualFS: the platform-independent filesystem logic (plan section 2.4).

Paths are POSIX-style strings as FUSE/WinFsp deliver them ("/USER/Demo/Person.cls.xml").
Every operation returns plain data or raises FsError(errno); the FUSE adapter is a thin wrapper.

Layout:  /                      namespaces
         /<NS>/                 package directories and documents (<name>.<ext>.xml)
Reading a document returns its XML export. Writing an *.xml file imports it when the last handle
closes - but only if data was actually written (a read-write open without writes imports nothing).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import logging
import os
import stat
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from irisfs.atelier.api import AtelierApi
from irisfs.atelier.errors import (
    AtelierError,
    AuthError,
    ConnectionFailed,
    ForbiddenError,
    NotFoundError,
)
from irisfs.atelier.models import DocInfo, NamespaceInfo
from irisfs.vfs import junk, xmlexport
from irisfs.vfs.content import ContentCache
from irisfs.vfs.errors import FsError
from irisfs.vfs.filters import is_visible
from irisfs.vfs.pathmap import doc_to_path, is_xml_name
from irisfs.vfs.tree import DirNode, FileNode, TreeCache, lookup

log = logging.getLogger(__name__)
Event = dict[str, Any]
Clock = Callable[[], float]

ENOATTR = getattr(errno, "ENOATTR", errno.ENODATA)
GHOST_TTL = 60.0


@dataclass(frozen=True)
class Options:
    read_only: bool = False
    show_system: bool = False
    compile_on_import: bool = True
    compile_flags: str = "cuk"
    tree_ttl: float = 10.0
    cache_bytes: int = 256 * 1024 * 1024
    case_insensitive: bool = False
    namespaces_ttl: float = 30.0
    prefetch_workers: int = 4  # background exports started by readdir (0 disables)

    @property
    def load_flags(self) -> str:
        return self.compile_flags if self.compile_on_import else "-c"


@dataclass(frozen=True)
class Attr:
    is_dir: bool
    size: int
    mtime: float
    mode: int  # permission bits only

    @property
    def st_mode(self) -> int:
        return (stat.S_IFDIR if self.is_dir else stat.S_IFREG) | self.mode


@dataclass
class WriteBuffer:
    """Content being written to one path; shared by all handles open on that path."""

    ns: str
    path: str
    data: bytearray
    dirty: bool = False
    refs: int = 0


@dataclass
class Handle:
    kind: str  # "read" | "write" | "scratch" | "ghost"
    path: str
    snapshot: bytes = b""
    buffer: WriteBuffer | None = None


@dataclass
class Ghost:
    """A just-imported file whose name matches no document: kept briefly so `cp` can stat it."""

    data: bytes
    expires: float


@dataclass
class _Scratch:
    data: bytearray = field(default_factory=bytearray)
    mtime: float = field(default_factory=time.time)


def split(path: str) -> tuple[str, ...]:
    return tuple(p for p in path.split("/") if p)


def parse_ts(ts: str, default: float) -> float:
    try:
        return dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return default


def to_fs_error(e: AtelierError) -> FsError:
    if isinstance(e, NotFoundError):
        return FsError(errno.ENOENT, e.message)
    if isinstance(e, AuthError | ForbiddenError):
        return FsError(errno.EACCES, e.message)
    if isinstance(e, ConnectionFailed):
        return FsError(errno.EIO, e.message)
    return FsError(errno.EIO, e.message)


class VirtualFS:
    def __init__(
        self,
        api: AtelierApi,
        options: Options | None = None,
        *,
        on_event: Callable[[Event], None] | None = None,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
    ) -> None:
        self.api = api
        self.opts = options or Options()
        self._emit = on_event or (lambda _e: None)
        self._clock = clock
        self._wall = wall_clock
        self.mounted_at = wall_clock()
        self._lock = threading.RLock()
        self._ns_info: dict[str, NamespaceInfo] = {}
        self._namespaces: tuple[float, tuple[str, ...]] | None = None
        self.trees = TreeCache(
            self._load_docs, ttl=self.opts.tree_ttl, case_insensitive=self.opts.case_insensitive, clock=clock
        )
        self.content = ContentCache(self.opts.cache_bytes)
        self._handles: dict[int, Handle] = {}
        self._next_fh = 1
        self._buffers: dict[str, WriteBuffer] = {}  # path -> buffer being written
        self._scratch: dict[str, _Scratch] = {}  # junk files (never sent to IRIS)
        self._ghosts: dict[str, Ghost] = {}
        self._prefetch = (
            ThreadPoolExecutor(self.opts.prefetch_workers, thread_name_prefix="prefetch")
            if self.opts.prefetch_workers > 0
            else None
        )

    # ==== server data =============================================================================
    def _call(self, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except AtelierError as e:
            log.warning("IRIS request failed: %s", e.message)
            raise to_fs_error(e) from e

    def namespaces(self) -> tuple[str, ...]:
        with self._lock:
            cached = self._namespaces
            if cached is not None and self._clock() - cached[0] < self.opts.namespaces_ttl:
                return cached[1]
        names = tuple(sorted(self._call(self.api.namespaces)))
        with self._lock:
            self._namespaces = (self._clock(), names)
        return names

    def _namespace(self, name: str) -> str | None:
        names = self.namespaces()
        if name in names:
            return name
        if self.opts.case_insensitive:
            folded = name.casefold()
            return next((n for n in names if n.casefold() == folded), None)
        return None

    def _info(self, ns: str) -> NamespaceInfo:
        with self._lock:
            info = self._ns_info.get(ns)
        if info is None:
            info = self._call(lambda: self.api.namespace_info(ns))
            with self._lock:
                self._ns_info[ns] = info
        return info

    def _load_docs(self, ns: str) -> list[DocInfo]:
        info = self._info(ns)
        docs: list[DocInfo] = self._call(lambda: self.api.list_docs(ns))
        return [d for d in docs if is_visible(d, info, show_system=self.opts.show_system)]

    def _export(self, ns: str, doc: DocInfo) -> bytes:
        data: bytes = self.content.get(
            (ns, doc.name, doc.ts), lambda: self._call(lambda: self.api.export_xml(ns, doc.name))
        )
        return data

    # ==== path resolution =========================================================================
    def _resolve(self, path: str) -> tuple[str | None, DirNode | FileNode | None]:
        """(namespace, node). For "/" returns (None, None); unknown paths raise ENOENT."""
        parts = split(path)
        if not parts:
            return None, None
        ns = self._namespace(parts[0])
        if ns is None:
            raise FsError(errno.ENOENT)
        tree = self._call(lambda: self.trees.get(ns))
        node = lookup(tree, parts[1:], case_insensitive=self.opts.case_insensitive)
        if node is None:
            raise FsError(errno.ENOENT)
        return ns, node

    def _expire_ghosts(self) -> None:
        now = self._clock()
        for p in [p for p, g in self._ghosts.items() if g.expires <= now]:
            del self._ghosts[p]

    def _dir_attr(self) -> Attr:
        return Attr(True, 0, self.mounted_at, 0o555 if self.opts.read_only else 0o755)

    def _file_attr(self, size: int, mtime: float) -> Attr:
        return Attr(False, size, mtime, 0o444 if self.opts.read_only else 0o644)

    # ==== read side ===============================================================================
    def getattr(self, path: str) -> Attr:
        with self._lock:
            self._expire_ghosts()
            if path in self._scratch:
                s = self._scratch[path]
                return self._file_attr(len(s.data), s.mtime)
            if path in self._buffers:
                return self._file_attr(len(self._buffers[path].data), self._wall())
            if path in self._ghosts:
                return self._file_attr(len(self._ghosts[path].data), self._wall())
        ns, node = self._resolve(path)
        if node is None or isinstance(node, DirNode):
            return self._dir_attr()
        assert ns is not None
        doc = node.doc
        size = self.content.size(
            (ns, doc.name, doc.ts), lambda: self._call(lambda: self.api.export_xml(ns, doc.name))
        )
        return self._file_attr(size, parse_ts(doc.ts, self.mounted_at))

    def readdir(self, path: str) -> list[str]:
        ns, node = self._resolve(path)
        if node is None and ns is None:
            names = list(self.namespaces())
        elif isinstance(node, DirNode):
            names = sorted(node.children)
            assert ns is not None
            self._prefetch_sizes(ns, node)
        else:
            raise FsError(errno.ENOTDIR)
        prefix = path.rstrip("/") + "/"
        with self._lock:
            self._expire_ghosts()
            extra = [
                p[len(prefix) :]
                for p in (*self._scratch, *self._buffers, *self._ghosts)
                if p.startswith(prefix) and "/" not in p[len(prefix) :]
            ]
        return sorted(set(names) | set(extra))

    def _prefetch_sizes(self, ns: str, node: DirNode) -> None:
        """Listing a folder is almost always followed by a stat of every entry, and a file's size is only
        known after exporting it. Start those exports now, in parallel, so the stats find them ready."""
        if self._prefetch is None:
            return
        for child in node.children.values():
            if isinstance(child, FileNode):
                doc = child.doc
                if not self.content.has_size((ns, doc.name, doc.ts)):
                    self._prefetch.submit(self._prefetch_one, ns, doc)

    def _prefetch_one(self, ns: str, doc: DocInfo) -> None:
        with contextlib.suppress(FsError):  # the foreground stat will retry and report the error
            self._export(ns, doc)

    def close(self) -> None:
        if self._prefetch is not None:
            self._prefetch.shutdown(wait=False, cancel_futures=True)

    def open(self, path: str, flags: int) -> int:
        writing = (flags & os.O_ACCMODE) in (os.O_WRONLY, os.O_RDWR) or bool(flags & os.O_TRUNC)
        with self._lock:
            if path in self._scratch:
                if flags & os.O_TRUNC:
                    self._scratch[path].data.clear()
                return self._new_handle(Handle("scratch", path))
            if path in self._buffers:
                if writing:
                    self._check_writable()
                buf = self._buffers[path]
                if flags & os.O_TRUNC:
                    buf.data.clear()
                    buf.dirty = True
                buf.refs += 1
                return self._new_handle(Handle("write", path, buffer=buf))
            if path in self._ghosts and not writing:
                return self._new_handle(Handle("ghost", path, snapshot=self._ghosts[path].data))
        ns, node = self._resolve(path)
        if node is None or isinstance(node, DirNode):
            raise FsError(errno.EISDIR)
        assert ns is not None
        if not writing:
            return self._new_handle(Handle("read", path, snapshot=self._export(ns, node.doc)))
        self._check_writable()
        initial = b"" if flags & os.O_TRUNC else self._export(ns, node.doc)
        with self._lock:
            existing = self._buffers.get(path)
            wbuf = existing or WriteBuffer(ns, path, bytearray(initial), dirty=bool(flags & os.O_TRUNC))
            self._buffers[path] = wbuf
            wbuf.refs += 1
            return self._new_handle(Handle("write", path, buffer=wbuf))

    def read(self, path: str, size: int, offset: int, fh: int) -> bytes:
        h = self._handle(fh)
        with self._lock:
            if h.kind == "scratch":
                return bytes(self._scratch[h.path].data[offset : offset + size])
            if h.kind == "write":
                assert h.buffer is not None
                return bytes(h.buffer.data[offset : offset + size])
        return h.snapshot[offset : offset + size]

    # ==== write side ==============================================================================
    def _check_writable(self) -> None:
        if self.opts.read_only:
            raise FsError(errno.EROFS)

    def create(self, path: str, mode: int) -> int:
        parts = split(path)
        filename = parts[-1] if parts else ""
        if junk.is_junk(filename):
            with self._lock:
                self._scratch[path] = _Scratch()
                return self._new_handle(Handle("scratch", path))
        self._check_writable()
        if len(parts) < 2:
            raise FsError(errno.EACCES, "files can only be created inside a namespace")
        if not is_xml_name(filename):
            raise FsError(errno.EACCES, "only .xml export files can be copied here")
        ns, parent = self._resolve("/" + "/".join(parts[:-1]))
        if not isinstance(parent, DirNode) or ns is None:
            raise FsError(errno.ENOTDIR)
        with self._lock:
            self._ghosts.pop(path, None)
            buf = self._buffers.get(path)
            if buf is None:
                buf = WriteBuffer(ns, path, bytearray(), dirty=True)
                self._buffers[path] = buf
            else:
                buf.data.clear()
                buf.dirty = True
            buf.refs += 1
            return self._new_handle(Handle("write", path, buffer=buf))

    def write(self, path: str, data: bytes, offset: int, fh: int) -> int:
        h = self._handle(fh)
        with self._lock:
            if h.kind == "scratch":
                target = self._scratch[h.path].data
            elif h.kind == "write" and h.buffer is not None:
                target = h.buffer.data
                h.buffer.dirty = True
            else:
                raise FsError(errno.EBADF)
            if offset > len(target):
                target.extend(b"\0" * (offset - len(target)))
            target[offset : offset + len(data)] = data
            return len(data)

    def truncate(self, path: str, length: int, fh: int | None = None) -> None:
        with self._lock:
            if path in self._scratch:
                _resize(self._scratch[path].data, length)
                return
            buf = self._buffers.get(path)
            if buf is not None:
                self._check_writable()
                _resize(buf.data, length)
                buf.dirty = True
                return
        # Truncating a document that is not open for writing (e.g. `truncate -s 0 file`)
        self._check_writable()
        _, node = self._resolve(path)
        if not isinstance(node, FileNode):
            raise FsError(errno.EISDIR)
        raise FsError(errno.EPERM, "open the file for writing to replace its content")

    def flush(self, path: str, fh: int) -> None:
        """Early validation so tools like `cp` report malformed XML as an I/O error."""
        h = self._handle(fh)
        if h.kind != "write" or h.buffer is None:
            return
        with self._lock:
            if not h.buffer.dirty or h.buffer.refs > 1:
                return
            data = bytes(h.buffer.data)
        try:
            xmlexport.validate(data)
        except xmlexport.InvalidExport as e:
            raise FsError(errno.EIO, str(e)) from e

    def release(self, path: str, fh: int) -> None:
        with self._lock:
            h = self._handles.pop(fh, None)
            if h is None or h.kind != "write" or h.buffer is None:
                return
            buf = h.buffer
            buf.refs -= 1
            if buf.refs > 0:
                return
            del self._buffers[buf.path]
            if not buf.dirty:
                return  # opened for writing but nothing written: never import
            data = bytes(buf.data)
        self._import(buf.ns, buf.path, data)

    def _import(self, ns: str, path: str, data: bytes) -> None:
        filename = split(path)[-1]
        try:
            manifest = xmlexport.validate(data)
        except xmlexport.InvalidExport as e:
            self._emit({"event": "import_failed", "ns": ns, "file": filename, "message": str(e)})
            return
        try:
            result = self.api.import_xml(ns, data, file=filename, flags=self.opts.load_flags)
        except AtelierError as e:
            self._emit({"event": "import_failed", "ns": ns, "file": filename, "message": e.message})
            return
        finally:
            for name in manifest.items:
                self.content.invalidate(ns, name)
            self.trees.invalidate(ns)
        if not result.imported:
            self._emit(
                {
                    "event": "import_failed",
                    "ns": ns,
                    "file": filename,
                    "message": result.error or "nothing imported",
                }
            )
            return
        self._emit(
            {
                "event": "imported",
                "ns": ns,
                "file": filename,
                "items": list(result.imported),
                "compiled": self.opts.compile_on_import,
                "compile_errors": [result.error] if result.error else [],
            }
        )
        canonical = {"/" + "/".join((ns, *p)) for p in (doc_to_path(n) for n in result.imported) if p}
        if path not in canonical:
            with self._lock:
                self._ghosts[path] = Ghost(data, self._clock() + GHOST_TTL)

    # ==== refused / no-op operations ==============================================================
    def unlink(self, path: str) -> None:
        with self._lock:
            if self._scratch.pop(path, None) is not None or self._ghosts.pop(path, None) is not None:
                return
        self._check_writable()
        self._resolve(path)  # ENOENT for unknown paths
        raise FsError(errno.EPERM, "deleting documents is not supported")

    def rename(self, old: str, new: str) -> None:
        with self._lock:
            if old in self._scratch and junk.is_junk(split(new)[-1] if split(new) else ""):
                self._scratch[new] = self._scratch.pop(old)
                return
        self._check_writable()
        raise FsError(errno.EPERM, "renaming is not supported")

    def mkdir(self, path: str, mode: int) -> None:
        self._check_writable()
        raise FsError(errno.EPERM, "creating folders is not supported")

    def rmdir(self, path: str) -> None:
        self._check_writable()
        raise FsError(errno.EPERM, "deleting folders is not supported")

    def chmod(self, path: str, mode: int) -> None:
        self._metadata_noop(path)

    def chown(self, path: str, uid: int, gid: int) -> None:
        self._metadata_noop(path)

    def utimens(self, path: str, times: tuple[int, int] | None = None) -> None:
        self._metadata_noop(path)

    def setxattr(self, path: str, name: str, value: bytes, options: int) -> None:
        self._metadata_noop(path)

    def removexattr(self, path: str, name: str) -> None:
        self._metadata_noop(path)

    def getxattr(self, path: str, name: str) -> bytes:
        self.getattr(path)
        raise FsError(ENOATTR)

    def listxattr(self, path: str) -> list[str]:
        self.getattr(path)
        return []

    def _metadata_noop(self, path: str) -> None:
        """Accept metadata changes so cp -p / Finder / Explorer copies succeed; nothing is stored."""
        with self._lock:
            if path in self._scratch or path in self._buffers or path in self._ghosts:
                return
        self._check_writable()
        self.getattr(path)

    def statfs(self) -> dict[str, int]:
        return {
            "f_bsize": 4096,
            "f_frsize": 4096,
            "f_blocks": 1 << 20,
            "f_bfree": 1 << 19,
            "f_bavail": 1 << 19,
            "f_files": 1 << 20,
            "f_ffree": 1 << 19,
            "f_favail": 1 << 19,
            "f_namemax": 255,
        }

    # ==== handles =================================================================================
    def _new_handle(self, h: Handle) -> int:
        fh = self._next_fh
        self._next_fh += 1
        self._handles[fh] = h
        return fh

    def _handle(self, fh: int) -> Handle:
        with self._lock:
            h = self._handles.get(fh)
        if h is None:
            raise FsError(errno.EBADF)
        return h

    def refresh(self, ns: str | None = None) -> None:
        """Drop cached listings (and namespace info) so the next access reloads from IRIS."""
        self.trees.invalidate(ns)
        with self._lock:
            self._namespaces = None
            if ns is None:
                self._ns_info.clear()


def _resize(data: bytearray, length: int) -> None:
    if length < len(data):
        del data[length:]
    else:
        data.extend(b"\0" * (length - len(data)))
