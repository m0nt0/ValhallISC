"""JSON-lines protocol between the GUI process and a mount worker (plan section 2.5).

parent -> worker (stdin):  {"cmd": "start", "profile": {...}, "password": "..."}
                           then {"cmd": "stop", "force": false}
worker -> parent (stdout): {"event": "mounted" | "unmounted" | "error" | "imported" | "import_failed", ...}
"""

from __future__ import annotations

import json
import threading
from typing import IO, Any

# error codes carried by {"event": "error", "code": ...}
AUTH_FAILED = "AUTH_FAILED"
UNREACHABLE = "UNREACHABLE"
SERVER_ERROR = "SERVER_ERROR"
FUSE_MISSING = "FUSE_MISSING"
MOUNTPOINT_INVALID = "MOUNTPOINT_INVALID"
MOUNT_FAILED = "MOUNT_FAILED"
BUSY = "BUSY"
BAD_CONFIG = "BAD_CONFIG"


def encode(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class EventWriter:
    """Thread-safe writer of event lines (FUSE callbacks run on many threads)."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        with self._lock:
            try:
                self._stream.write(encode(event))
                self._stream.flush()
            except (OSError, ValueError):
                pass  # parent went away; nothing useful to do
