"""Folder listings loaded one level at a time, each cached with a TTL (ADR-014).

A namespace is no longer listed as a whole: opening a folder loads only that folder, so the first
look at a namespace with tens of thousands of documents costs one small query instead of all of them.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from irisfs.atelier.models import DocInfo
from irisfs.vfs.tree import DirNode, FileNode

log = logging.getLogger(__name__)
Clock = Callable[[], float]
Key = tuple[str, tuple[str, ...]]  # (namespace, folder path parts)
# child name -> the document for a file, None for a sub-folder
Listing = dict[str, DocInfo | None]
_UNSEEN = object()


class FolderCache:
    """`loader(ns, parts)` returns the visible content of one folder (`parts` are canonical names)."""

    def __init__(
        self,
        loader: Callable[[str, tuple[str, ...]], Listing],
        *,
        ttl: float = 10.0,
        case_insensitive: bool = False,
        clock: Clock = time.monotonic,
        wall_clock: Clock = time.time,
    ) -> None:
        self._loader = loader
        self.ttl = ttl
        self.case_insensitive = case_insensitive
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._key_locks: dict[Key, threading.Lock] = {}
        self._listings: dict[Key, tuple[float, DirNode]] = {}
        # Per folder: signature of its last listing (None after a failed load).
        self._signatures: dict[Key, int | None] = {}
        # Per namespace: wall time any of its folders last changed. It is the mtime of every folder of the
        # namespace: NFS-based FUSE (FUSE-T) keeps cached lookups - including "not found" answers given
        # during an outage - until a folder's mtime moves (ADR-013).
        self._changed: dict[str, float] = {}

    # ---- listings ------------------------------------------------------------------------------
    def get(self, ns: str, parts: tuple[str, ...]) -> DirNode:
        """The loaded folder: files as FileNode, sub-folders as empty (not yet loaded) DirNode."""
        key = (ns, parts)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:  # one loader call per folder at a time
            cached = self._listings.get(key)
            if cached is not None and self._clock() - cached[0] < self.ttl:
                return cached[1]
            try:
                listing = self._loader(ns, parts)
            except Exception:
                with self._lock:
                    self._signatures[key] = None  # the next successful load counts as a change
                    self._changed.setdefault(ns, self._wall())
                raise
            node = DirNode(
                {name: DirNode() if doc is None else FileNode(doc) for name, doc in sorted(listing.items())}
            )
            signature = hash(tuple(sorted((n, d.ts if d else "") for n, d in listing.items())))
            with self._lock:
                previous = self._signatures.get(key, _UNSEEN)
                if ns not in self._changed:
                    self._changed[ns] = self._wall()
                elif previous is not _UNSEEN and previous != signature:
                    self._bump(ns)
                self._signatures[key] = signature
                self._listings[key] = (self._clock(), node)
            return node

    def resolve(
        self, ns: str, parts: tuple[str, ...], *, load_last: bool = True
    ) -> DirNode | FileNode | None:
        """The node at `parts` inside namespace `ns`, or None if it does not exist.

        Every folder on the way is loaded (from cache when fresh). With `load_last=False` a folder at the
        end of the path is returned unloaded (enough to know that it exists and is a folder)."""
        node: DirNode = self.get(ns, ())
        canonical: list[str] = []
        for i, segment in enumerate(parts):
            name = self._match(node, segment)
            if name is None:
                return None
            child = node.children[name]
            last = i == len(parts) - 1
            if isinstance(child, FileNode):
                return child if last else None
            canonical.append(name)
            if last and not load_last and not self.was_listed(ns, tuple(canonical)):
                # Never listed, so no client can hold a stale copy of its content: no need to load it.
                # A folder listed before is reloaded (TTL): its stat must show that it changed.
                return child
            node = self.get(ns, tuple(canonical))
        return node

    def _match(self, node: DirNode, name: str) -> str | None:
        if name in node.children:
            return name
        if not self.case_insensitive:
            return None
        folded = name.casefold()
        return next((k for k in node.children if k.casefold() == folded), None)

    # ---- change tracking -----------------------------------------------------------------------
    def was_listed(self, ns: str, parts: tuple[str, ...]) -> bool:
        with self._lock:
            return (ns, parts) in self._signatures

    def _bump(self, ns: str) -> None:
        changed = self._wall()
        previous = self._changed.get(ns)
        if previous is not None and changed <= previous:
            changed = previous + 1.0  # strictly increasing even within the same second
        self._changed[ns] = changed

    def changed_at(self, ns: str) -> float | None:
        """Wall time a folder of the namespace last changed (None before the first load)."""
        with self._lock:
            return self._changed.get(ns)

    def invalidate(self, ns: str | None = None) -> None:
        """Drop cached listings (all, or one namespace's). Signatures stay, so a listing that comes back
        different still moves the namespace's change time."""
        with self._lock:
            for key in [k for k in self._listings if ns is None or k[0] == ns]:
                del self._listings[key]
