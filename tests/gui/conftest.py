"""wx smoke tests: need wxPython and a display (Xvfb in Docker). Skipped otherwise, unless
IRISFS_REQUIRE_GUI=1 (then a missing display fails)."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="session")
def wx_app() -> Iterator[Any]:
    try:
        import wx
    except ImportError as e:
        if os.environ.get("IRISFS_REQUIRE_GUI") == "1":
            pytest.fail(f"wxPython missing: {e}")
        pytest.skip("wxPython not installed")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        if os.environ.get("IRISFS_REQUIRE_GUI") == "1":
            pytest.fail("no DISPLAY (run under xvfb-run)")
        pytest.skip("no display")
    app = wx.App(False)
    yield app
    app.Destroy()


def pump(app: Any) -> None:
    """Run pending wx events (CallAfter etc.)."""
    import wx

    for _ in range(5):
        app.ProcessPendingEvents()
        wx.SafeYield()
