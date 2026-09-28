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
    NotExportableError,
    NotFoundError,
    ServerError,
)
from irisfs.atelier.models import DocInfo, FolderEntry, NamespaceInfo
from irisfs.vfs import junk, xmlexport
from irisfs.vfs.content import ContentCache
from irisfs.vfs.errors import FsError
from irisfs.vfs.filters import is_system_name, is_visible
from irisfs.vfs.folders import FolderCache, Listing
from irisfs.vfs.pathmap import doc_to_path, is_xml_name
from irisfs.vfs.tree import DirNode, FileNode, TreeCache, lookup

log = logging.getLogger(__name__)
Event = dict[str, Any]
Clock = Callable[[], float]
# access-mode bits of open() flags; Windows' os module has no O_ACCMODE, but WinFsp uses the same 0/1/2 values
O_ACCMODE = getattr(os, "O_ACCMODE", os.O_RDONLY | os.O_WRONLY | os.O_RDWR)

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
    background_refresh: bool = True  # expired folder listings are served stale and reloaded in background
    no_index_marker: bool = False  # macOS: expose /.metadata_never_index so Spotlight skips the volume

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
    expires: float | None = None  # set while an empty, closed file waits to be written (placeholder)


@dataclass
class Handle:
    kind: str  # "read" | "write" | "scratch" | "ghost"
    path: str
    snapshot: bytes = b""
    buffer: WriteBuffer | None = None
    scratch: _Scratch | None = None


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


def _full_name(package: str, entry: FolderEntry) -> str:
    """What the system-name rule tests: a document's name, or a package's name with a trailing dot."""
    if not entry.is_dir:
        return entry.name
    return f"{package}.{entry.name}." if package else f"{entry.name}."


def _category(name: str) -> str:
    ext = name.rpartition(".")[2].lower()
    if ext == "cls":
        return "CLS"
    return "RTN" if ext in ("mac", "int", "inc", "bas", "mvb", "mvi") else "OTH"


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
        # Folders are listed one level at a time (ADR-014).
        self._refresher = (
            ThreadPoolExecutor(2, thread_name_prefix="refresh") if self.opts.background_refresh else None
        )
        self.folders = FolderCache(
            self._load_folder,
            ttl=self.opts.tree_ttl,
            case_insensitive=self.opts.case_insensitive,
            clock=clock,
            wall_clock=wall_clock,
            background=self._refresher.submit if self._refresher else None,
        )
        # Fallback for namespaces where the one-level query fails: the whole namespace, as before ADR-014.
        self.trees = TreeCache(
            self._load_docs,
            ttl=self.opts.tree_ttl,
            case_insensitive=self.opts.case_insensitive,
            clock=clock,
            wall_clock=wall_clock,
        )
        self._full_listing: set[str] = set()
        # Per namespace: "other" documents (lookup tables, DTL, ...) that belong inside a package folder.
        # IRIS lists them only at the root, by full name.
        self._nested_others: dict[str, list[tuple[tuple[str, ...], DocInfo]]] = {}
        # (namespace, package or document name, is package) -> mapped from a user database (shown)?
        self._mapped_visible: dict[tuple[str, str, bool], bool] = {}
        # (namespace, document, timestamp) of classes in deployed mode: IRIS refuses to export them
        self._deployed: set[tuple[str, str, str]] = set()
        self.content = ContentCache(self.opts.cache_bytes)
        self._handles: dict[int, Handle] = {}
        self._next_fh = 1
        self._buffers: dict[str, WriteBuffer] = {}  # path -> buffer being written
        self._scratch: dict[str, _Scratch] = {}  # junk files (never sent to IRIS)
        self._ghosts: dict[str, Ghost] = {}
        if self.opts.no_index_marker:
            self._scratch["/.metadata_never_index"] = _Scratch()
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
        """Every visible document of a namespace (fallback listing, see `_load_folder`)."""
        info = self._info(ns)
        docs: list[DocInfo] = self._call(lambda: self.api.list_docs(ns))
        return [d for d in docs if is_visible(d, info, show_system=self.opts.show_system)]

    def _load_folder(self, ns: str, parts: tuple[str, ...]) -> Listing:
        """The visible content of one folder. Uses the one-level query; if the server refuses it (for
        example the account may not run that SQL procedure), falls back to listing the whole namespace."""
        with self._lock:
            full = ns in self._full_listing
        if not full:
            try:
                return self._query_folder(ns, parts)
            except ServerError as e:
                log.warning("one-level listing failed in %s (%s): listing the whole namespace", ns, e.message)
                with self._lock:
                    self._full_listing.add(ns)
            except AtelierError as e:
                log.warning("IRIS request failed: %s", e.message)
                raise to_fs_error(e) from e
        tree = self._call(lambda: self.trees.get(ns))
        node = lookup(tree, parts, case_insensitive=self.opts.case_insensitive)
        if not isinstance(node, DirNode):
            return {}
        return {n: c.doc if isinstance(c, FileNode) else None for n, c in node.children.items()}

    def _query_folder(self, ns: str, parts: tuple[str, ...]) -> Listing:
        show = self.opts.show_system
        package = ".".join(parts)
        entries = self.api.list_folder(
            ns, package, system=show, generated=False, mapped=True
        )  # never, as before
        if not show:
            entries = [e for e in entries if not is_system_name(_full_name(package, e))]
            local = self.api.list_folder(ns, package, system=False, generated=False, mapped=False)
            local_names = {e.name for e in local}
            mapped_only = [e for e in entries if e.name not in local_names]
            if mapped_only:
                hidden = self._from_system_databases(ns, package, mapped_only)
                entries = [e for e in entries if e.name not in hidden]
        depth = len(parts)
        listing: Listing = {}
        nested: list[tuple[tuple[str, ...], DocInfo]] = []
        for e in entries:
            if e.is_dir:
                listing.setdefault(e.name, None)
                continue
            path = doc_to_path(e.name)
            if path is None or path[:depth] != parts:
                continue
            doc = DocInfo(e.name, _category(e.name), e.ts, db="", upd=True, gen=False)
            if len(path) == depth + 1:
                listing[path[-1]] = doc
            else:  # an "other" document listed at the root by its full name
                listing.setdefault(path[depth], None)
                nested.append((path, doc))
        if not parts:
            with self._lock:
                self._nested_others[ns] = nested
        else:
            with self._lock:
                others = self._nested_others.get(ns, [])
            for path, doc in others:
                if len(path) > depth and path[:depth] == parts:
                    if len(path) == depth + 1:
                        listing[path[-1]] = doc
                    else:
                        listing.setdefault(path[depth], None)
        return listing

    def _from_system_databases(self, ns: str, package: str, mapped_only: list[FolderEntry]) -> set[str]:
        """Names of the mapped entries that come from a system database (hidden, like ADR-003's rule)."""
        info = self._info(ns)
        if all(d.dbsys for d in info.databases if not d.default):
            return {e.name for e in mapped_only}  # every mapped database is a system one
        # Some mapped database is a user one: ask IRIS where each mapped entry comes from. Everything
        # inside a package mapped from a user database comes from that database too.
        hidden: set[str] = set()
        unknown: list[tuple[FolderEntry, str]] = []
        for e in mapped_only:
            if e.is_dir:
                name = f"{package}.{e.name}" if package else e.name
                stem = name
            else:
                name, stem = e.name, e.name.rpartition(".")[0]
            segments = stem.split(".")
            with self._lock:
                inherited = any(
                    self._mapped_visible.get((ns, ".".join(segments[:i]), True))
                    for i in range(1, len(segments))
                )
                visible = True if inherited else self._mapped_visible.get((ns, name, e.is_dir))
            if visible is None:
                unknown.append((e, name))
            elif not visible:
                hidden.add(e.name)
        if unknown:
            # Independent lookups: run them side by side (the client caps concurrent requests).
            with ThreadPoolExecutor(min(4, len(unknown)), thread_name_prefix="classify") as pool:
                results = list(pool.map(lambda item: self._mapped_is_visible(ns, info, *item), unknown))
            hidden |= {e.name for (e, _), visible in zip(unknown, results, strict=True) if not visible}
        return hidden

    def _mapped_is_visible(self, ns: str, info: NamespaceInfo, e: FolderEntry, name: str) -> bool:
        sample = self._sample_document(ns, name) if e.is_dir else name
        if sample is None:
            visible = False  # nothing inside that we could show
        else:
            doc: DocInfo = self._call(lambda: self.api.doc_info(ns, sample))
            visible = is_visible(doc, info, show_system=False)
        log.debug("mapped %s %s in %s: %s", "package" if e.is_dir else "item", name, ns, visible)
        with self._lock:
            self._mapped_visible[(ns, name, e.is_dir)] = visible
        return visible

    def _sample_document(self, ns: str, package: str, depth: int = 0) -> str | None:
        """One document inside a mapped package (its database tells where the whole package comes from).
        A few one-level queries instead of listing the package with `docnames`, whose cost grows with it."""
        entries: list[FolderEntry] = self._call(
            lambda: self.api.list_folder(ns, package, system=True, generated=False, mapped=True)
        )
        doc = next((e for e in entries if not e.is_dir), None)
        if doc is not None:
            return doc.name
        if depth >= 20:
            return None
        for sub in (e for e in entries if e.is_dir):
            found = self._sample_document(ns, f"{package}.{sub.name}", depth + 1)
            if found is not None:
                return found
        return None

    def _export(self, ns: str, doc: DocInfo) -> bytes:
        data: bytes = self.content.get((ns, doc.name, doc.ts), lambda: self._fetch(ns, doc))
        return data

    def _fetch(self, ns: str, doc: DocInfo) -> bytes:
        try:
            return self.api.export_xml(ns, doc.name)
        except NotExportableError as e:
            # Nothing to export (deployed class, default Studio project): shown read-only and empty, and it
            # can be neither read nor replaced. Remembered, so IRIS is not asked again.
            log.info("%s in %s cannot be exported, shown read-only: %s", doc.name, ns, e.message)
            with self._lock:
                self._deployed.add((ns, doc.name, doc.ts))
            raise FsError(errno.EACCES, e.message) from e
        except AtelierError as e:
            log.warning("IRIS request failed: %s", e.message)
            raise to_fs_error(e) from e

    def _is_deployed(self, ns: str, doc: DocInfo) -> bool:
        with self._lock:
            return (ns, doc.name, doc.ts) in self._deployed

    # ==== path resolution =========================================================================
    def _resolve(self, path: str, *, load_last: bool = True) -> tuple[str | None, DirNode | FileNode | None]:
        """(namespace, node). For "/" returns (None, None); unknown paths raise ENOENT.
        A folder is returned loaded (its children listed) unless `load_last` is False."""
        parts = split(path)
        if not parts:
            return None, None
        if parts[0].startswith("."):
            raise FsError(errno.ENOENT)  # Finder's probes (._., .DS_Store, ...): no namespace starts with "."
        ns = self._namespace(parts[0])
        if ns is None:
            raise FsError(errno.ENOENT)
        node = self._call(lambda: self.folders.resolve(ns, parts[1:], load_last=load_last))
        if node is None:
            raise FsError(errno.ENOENT)
        return ns, node

    def _expire_ghosts(self) -> None:
        now = self._clock()
        for p in [p for p, g in self._ghosts.items() if g.expires <= now]:
            del self._ghosts[p]
        for p in [
            p for p, b in self._buffers.items() if b.refs == 0 and b.expires is not None and b.expires <= now
        ]:
            del self._buffers[p]  # empty placeholder that was never written

    def _dir_attr(self, ns: str | None = None) -> Attr:
        # a namespace's folders carry the time one of its listings last changed (see FolderCache.changed_at)
        mtime = (self.folders.changed_at(ns) if ns else None) or self.mounted_at
        return Attr(True, 0, mtime, 0o555 if self.opts.read_only else 0o755)

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
        ns, node = self._resolve(path, load_last=False)  # a folder's stat does not need its content
        if node is None or isinstance(node, DirNode):
            return self._dir_attr(ns)
        assert ns is not None
        doc = node.doc
        mtime = parse_ts(doc.ts, self.mounted_at)
        if not self._is_deployed(ns, doc):
            try:
                size = self.content.size((ns, doc.name, doc.ts), lambda: self._fetch(ns, doc))
                return self._file_attr(size, mtime)
            except FsError:
                if not self._is_deployed(ns, doc):
                    raise
        return Attr(False, 0, mtime, 0o444)  # deployed class: read-only, no content

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
                if not self.content.has_size((ns, doc.name, doc.ts)) and not self._is_deployed(ns, doc):
                    self._prefetch.submit(self._prefetch_one, ns, doc)

    def _prefetch_one(self, ns: str, doc: DocInfo) -> None:
        with contextlib.suppress(FsError):  # the foreground stat will retry and report the error
            self._export(ns, doc)

    def close(self) -> None:
        for pool in (self._prefetch, self._refresher):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

    def open(self, path: str, flags: int) -> int:
        writing = (flags & O_ACCMODE) in (os.O_WRONLY, os.O_RDWR) or bool(flags & os.O_TRUNC)
        with self._lock:
            if path in self._scratch:
                scratch = self._scratch[path]
                if flags & os.O_TRUNC:
                    scratch.data.clear()
                return self._new_handle(Handle("scratch", path, scratch=scratch))
            if path in self._buffers:
                if writing:
                    self._check_writable()
                buf = self._buffers[path]
                if flags & os.O_TRUNC:
                    buf.data.clear()
                    buf.dirty = True
                buf.refs += 1
                buf.expires = None
                return self._new_handle(Handle("write", path, buffer=buf))
            if path in self._ghosts:
                if not writing:
                    return self._new_handle(Handle("ghost", path, snapshot=self._ghosts[path].data))
                # Copying the same file in again while its ghost is still listed: write it anew.
                self._check_writable()
                ghost = self._ghosts.pop(path)
                parts = split(path)
                ns_name = self._namespace(parts[0]) if parts else None
                if ns_name is None:
                    raise FsError(errno.ENOENT)
                truncate = bool(flags & os.O_TRUNC)
                wb = WriteBuffer(
                    ns_name, path, bytearray(b"" if truncate else ghost.data), dirty=truncate, refs=1
                )
                self._buffers[path] = wb
                return self._new_handle(Handle("write", path, buffer=wb))
        ns, node = self._resolve(path)
        if node is None or isinstance(node, DirNode):
            raise FsError(errno.EISDIR)
        assert ns is not None
        if self._is_deployed(ns, node.doc):
            raise FsError(errno.EACCES, "deployed class: no source to read, and it cannot be replaced")
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
            if h.kind == "scratch" and h.scratch is not None:
                return bytes(h.scratch.data[offset : offset + size])
            if h.kind == "write":
                assert h.buffer is not None
                return bytes(h.buffer.data[offset : offset + size])
        return h.snapshot[offset : offset + size]

    # ==== write side ==============================================================================
    def _check_writable(self) -> None:
        if self.opts.read_only:
            raise FsError(errno.EROFS)

    def _new_scratch(self, path: str) -> int:
        with self._lock:
            scratch = _Scratch()
            self._scratch[path] = scratch
            return self._new_handle(Handle("scratch", path, scratch=scratch))

    def _target_namespace(self, parts: tuple[str, ...]) -> str:
        """Namespace for a new file at `parts`; its parent folder must exist."""
        if len(parts) < 2:
            raise FsError(errno.EACCES, "files can only be created inside a namespace")
        ns, parent = self._resolve("/" + "/".join(parts[:-1]))
        if not isinstance(parent, DirNode) or ns is None:
            raise FsError(errno.ENOTDIR)
        return ns

    def create(self, path: str, mode: int) -> int:
        parts = split(path)
        filename = parts[-1] if parts else ""
        if junk.is_junk(filename):
            return self._new_scratch(path)
        self._check_writable()
        if filename.startswith(".") and not is_xml_name(filename):
            # Hidden temporary file (ditto/Finder ".BC.T_*", editors' atomic saves): kept in memory and
            # imported only if it is renamed to an *.xml name.
            self._target_namespace(parts)
            return self._new_scratch(path)
        if not is_xml_name(filename):
            log.debug("refusing to create %s: not an .xml file", path)
            raise FsError(errno.EACCES, "only .xml export files can be copied here")
        ns = self._target_namespace(parts)
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
            if h.kind == "scratch" and h.scratch is not None:
                target = h.scratch.data
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
            if not h.buffer.dirty or h.buffer.refs > 1 or not h.buffer.data:
                return  # empty: the writer may still be coming back (see release)
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
            if buf.dirty and not buf.data:
                # Finder creates the file, closes it empty, then reopens it to write the content.
                # Keep an empty placeholder instead of importing (or dropping) it.
                buf.expires = self._clock() + GHOST_TTL
                return
            del self._buffers[buf.path]
            if not buf.dirty:
                return  # opened for writing but nothing written: never import
            data = bytes(buf.data)
        self._import(buf.ns, buf.path, data)

    def _import(self, ns: str, path: str, data: bytes) -> bool:
        """Import `data` as the file at `path`. Returns False if it is not a valid IRIS export.
        Server-side outcomes (imported / failed / compile errors) are reported through events."""
        filename = split(path)[-1]
        try:
            manifest = xmlexport.validate(data)
        except xmlexport.InvalidExport as e:
            self._emit({"event": "import_failed", "ns": ns, "file": filename, "message": str(e)})
            return False
        try:
            result = self.api.import_xml(ns, data, file=filename, flags=self.opts.load_flags)
        except AtelierError as e:
            self._emit({"event": "import_failed", "ns": ns, "file": filename, "message": e.message})
            return True
        finally:
            for name in manifest.items:
                self.content.invalidate(ns, name)
            self.folders.invalidate(ns)
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
            return True
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
        return True

    # ==== refused / no-op operations ==============================================================
    def unlink(self, path: str) -> None:
        with self._lock:
            if self._scratch.pop(path, None) is not None or self._ghosts.pop(path, None) is not None:
                return
            placeholder = self._buffers.get(path)
            if placeholder is not None and placeholder.refs == 0:
                del self._buffers[path]
                return
        self._check_writable()
        self._resolve(path)  # ENOENT for unknown paths
        raise FsError(errno.EPERM, "deleting documents is not supported")

    def rename(self, old: str, new: str) -> None:
        """Only in-memory files can be renamed. Renaming one to an *.xml name imports it: that is how
        ditto/Finder (".BC.T_*" temp files) and editors doing atomic saves deliver content."""
        log.debug("rename %s -> %s", old, new)
        new_parts = split(new)
        new_name = new_parts[-1] if new_parts else ""
        stays_scratch = junk.is_junk(new_name) or (new_name.startswith(".") and not is_xml_name(new_name))
        with self._lock:
            scratch = self._scratch.get(old)
            if scratch is not None and stays_scratch:
                self._scratch[new] = self._scratch.pop(old)
                return
        self._check_writable()
        if scratch is None or not is_xml_name(new_name) or junk.is_junk(new_name):
            raise FsError(errno.EPERM, "renaming is not supported")
        ns = self._target_namespace(new_parts)
        with self._lock:
            self._scratch.pop(old, None)
            self._ghosts.pop(new, None)
            data = bytes(scratch.data)
        if not self._import(ns, new, data):
            raise FsError(errno.EIO, "not a valid IRIS XML export")

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
        self.folders.invalidate(ns)
        self.trees.invalidate(ns)
        with self._lock:
            self._namespaces = None
            for key in [k for k in self._mapped_visible if ns is None or k[0] == ns]:
                del self._mapped_visible[key]
            if ns is None:
                self._ns_info.clear()
                self._full_listing.clear()
            else:
                self._full_listing.discard(ns)


def _resize(data: bytearray, length: int) -> None:
    if length < len(data):
        del data[length:]
    else:
        data.extend(b"\0" * (length - len(data)))
