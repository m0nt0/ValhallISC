"""Logging setup: rotating file in the user log dir, optional stderr."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import platformdirs

from irisfs import APP_NAME

_FORMAT = "%(asctime)s %(process)d %(threadName)s %(levelname)s %(name)s: %(message)s"


def log_dir() -> Path:
    return Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def setup(name: str, *, verbose: bool = False, to_stderr: bool = True) -> Path:
    """Configure root logging; returns the log file path. `name` distinguishes gui/worker logs."""
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.log"
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # httpx/httpcore log every request (with URLs) at INFO/DEBUG; our client logs what matters.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(fh)
    if to_stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)
    return path
