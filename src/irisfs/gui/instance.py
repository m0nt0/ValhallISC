"""Talking to the instance already running (ADR-016).

Launching ValhallISC while it runs must bring its window forward, not fail. On macOS, Finder, the Dock and
Launchpad reopen the running app (wx.App.MacReopenApp); a second process (any platform, or the command line)
drops a request file in the settings folder instead, and the running instance polls for it.
"""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)
REQUEST_FILE = "show-profiles.request"


def request_show(config_dir: Path) -> None:
    """Ask the running instance to show its Profiles window."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / REQUEST_FILE).write_text(f"{time.time()}\n", encoding="utf-8")


def take_show_request(config_dir: Path) -> bool:
    """True (once) if another launch asked for the window."""
    path = config_dir / REQUEST_FILE
    if not path.exists():
        return False
    with contextlib.suppress(OSError):
        path.unlink()
    return True
