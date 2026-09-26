"""Directory trees built from `docnames`, one per namespace, cached with a TTL."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from irisfs.atelier.models import DocInfo
from irisfs.vfs.pathmap import doc_to_path

log = logging.getLogger(__name__)
Clock = Callable[[], float]


@dataclass
class DirNode:
    children: dict[str, DirNode | FileNode] = field(default_factory=dict)


@dataclass(frozen=True)
class FileNode:
    doc: DocInfo


Node = DirNode | FileNode


def build_tree(docs: list[DocInfo], *, case_insensitive: bool) -> DirNode:
    """Build the directory tree. On case-insensitive systems the first of two names differing only
    by case wins (IRIS forbids this for classes, but routines/documents could still collide)."""
    root = DirNode()
    for doc in sorted(docs, key=lambda d: d.name):
        parts = doc_to_path(doc.name)
        if parts is None:
            continue
        node = root
        ok = True
        for segment in parts[:-1]:
            child = _child(node, segment, case_insensitive)
            if child is None:
                child = DirNode()
                node.children[segment] = child
            if not isinstance(child, DirNode):
                ok = False
                break
            node = child
        if not ok or _child(node, parts[-1], case_insensitive) is not None:
            log.warning("skipping %s: its path collides with another item", doc.name)
            continue
        node.children[parts[-1]] = FileNode(doc)
    return root


def _child(node: DirNode, name: str, case_insensitive: bool) -> Node | None:
    found = node.children.get(name)
    if found is not None or not case_insensitive:
        return found
    folded = name.casefold()
    return next((v for k, v in node.children.items() if k.casefold() == folded), None)


def lookup(root: DirNode, parts: tuple[str, ...], *, case_insensitive: bool) -> Node | None:
    node: Node = root
    for segment in parts:
        if not isinstance(node, DirNode):
            return None
        child = _child(node, segment, case_insensitive)
        if child is None:
            return None
        node = child
    return node


class TreeCache:
    """Per-namespace trees. `loader(ns)` returns the visible documents of a namespace."""

    def __init__(
        self,
        loader: Callable[[str], list[DocInfo]],
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
        self._ns_locks: dict[str, threading.Lock] = {}
        self._trees: dict[str, tuple[float, DirNode]] = {}
        # Per namespace: (listing signature, wall time it last changed). The change time is used as the mtime
        # of the namespace's folders: NFS-based FUSE (FUSE-T) keeps cached lookups - including "not found"
        # answers given during an outage - until a folder's mtime moves.
        self._changed: dict[str, tuple[int | None, float]] = {}

    def get(self, ns: str) -> DirNode:
        with self._lock:
            ns_lock = self._ns_locks.setdefault(ns, threading.Lock())
        with ns_lock:  # one loader call per namespace at a time
            cached = self._trees.get(ns)
            if cached is not None and self._clock() - cached[0] < self.ttl:
                return cached[1]
            try:
                docs = self._loader(ns)
            except Exception:
                # a failed load counts as a change: the next successful one must bump the mtime again
                previous = self._changed.get(ns)
                self._changed[ns] = (None, previous[1] if previous else self._wall())
                raise
            signature = hash(tuple(sorted((d.name, d.ts) for d in docs)))
            previous = self._changed.get(ns)
            if previous is None or previous[0] != signature:
                changed = self._wall()
                if previous is not None and changed <= previous[1]:
                    changed = previous[1] + 1.0  # strictly increasing even within the same second
                self._changed[ns] = (signature, changed)
            tree = build_tree(docs, case_insensitive=self.case_insensitive)
            self._trees[ns] = (self._clock(), tree)
            return tree

    def changed_at(self, ns: str) -> float | None:
        """Wall time the namespace's listing last changed (None before the first load)."""
        entry = self._changed.get(ns)
        return entry[1] if entry else None

    def invalidate(self, ns: str | None = None) -> None:
        with self._lock:
            if ns is None:
                self._trees.clear()
            else:
                self._trees.pop(ns, None)
