"""LRU cache of XML exports keyed by (namespace, document, timestamp), bounded by total bytes.

A new timestamp means a new key, so stale content is never served once the tree has been refreshed.
Concurrent requests for the same key share one export (single flight).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable

Key = tuple[str, str, str]


class ContentCache:
    def __init__(self, max_bytes: int = 256 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._data: OrderedDict[Key, bytes] = OrderedDict()
        self._sizes: dict[Key, int] = {}  # survives eviction: getattr needs sizes, not content
        self._inflight: dict[Key, threading.Lock] = {}
        self._total = 0

    def get(self, key: Key, loader: Callable[[], bytes]) -> bytes:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            flight = self._inflight.setdefault(key, threading.Lock())
        with flight:
            with self._lock:
                if key in self._data:
                    self._data.move_to_end(key)
                    return self._data[key]
            try:
                data = loader()
                with self._lock:
                    self._store(key, data)
            finally:
                with self._lock:
                    self._inflight.pop(key, None)
            return data

    def size(self, key: Key, loader: Callable[[], bytes]) -> int:
        with self._lock:
            if key in self._sizes:
                return self._sizes[key]
        return len(self.get(key, loader))

    def has_size(self, key: Key) -> bool:
        with self._lock:
            return key in self._sizes or key in self._inflight

    def _store(self, key: Key, data: bytes) -> None:
        self._sizes[key] = len(data)
        if len(data) > self.max_bytes:
            return
        self._data[key] = data
        self._total += len(data)
        while self._total > self.max_bytes:
            _, evicted = self._data.popitem(last=False)
            self._total -= len(evicted)

    def invalidate(self, ns: str, name: str | None = None) -> None:
        with self._lock:
            for key in [k for k in self._data if k[0] == ns and (name is None or k[1] == name)]:
                self._total -= len(self._data.pop(key))
            for key in [k for k in self._sizes if k[0] == ns and (name is None or k[1] == name)]:
                del self._sizes[key]

    @property
    def total_bytes(self) -> int:
        return self._total
