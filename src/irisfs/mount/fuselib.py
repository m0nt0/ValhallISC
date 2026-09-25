"""Locate the platform FUSE library before `mfusepy` is imported.

`mfusepy` resolves the library at import time (honouring FUSE_LIBRARY_PATH) and raises if none
is found, so callers must use `ensure_loaded()` instead of importing it directly.
"""

from __future__ import annotations

import ctypes.util
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

# Search order on macOS: FUSE-T, then macFUSE (official pkg), MacPorts, Homebrew.
MACOS_CANDIDATES = (
    ("FUSE-T", "/usr/local/lib/libfuse-t.dylib"),
    ("macFUSE", "/usr/local/lib/libfuse.2.dylib"),
    ("macFUSE (MacPorts)", "/opt/local/lib/libfuse.2.dylib"),
    ("macFUSE (Homebrew)", "/opt/homebrew/lib/libfuse.2.dylib"),
)
MACFUSE_BUNDLE = Path("/Library/Filesystems/macfuse.fs")

INSTALL_HINTS = {
    "Darwin": "Install FUSE-T (https://www.fuse-t.org) or macFUSE (https://macfuse.github.io).",
    "Linux": "Install the fuse3 package (e.g. 'apt install fuse3' or 'dnf install fuse3').",
    "Windows": "Install WinFsp (https://winfsp.dev/rel/).",
}


class FuseNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class FuseLibrary:
    kind: str
    path: str
    problem: str | None = None  # set when the library exists but is unlikely to mount


def _windows_winfsp() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0, winreg.KEY_READ | view
            ) as k:
                install_dir = str(winreg.QueryValueEx(k, "InstallDir")[0])
        except OSError:
            continue
        arch = {"AMD64": "x64", "ARM64": "a64"}.get(platform.machine().upper(), "x86")
        dll = Path(install_dir) / "bin" / f"winfsp-{arch}.dll"
        if dll.exists():
            return str(dll)
    return None


def find_library(system: str | None = None) -> FuseLibrary | None:
    """Return the FUSE library to use, or None. Honours an explicit FUSE_LIBRARY_PATH."""
    system = system or platform.system()
    explicit = os.environ.get("FUSE_LIBRARY_PATH")
    if explicit:
        return FuseLibrary(
            "FUSE_LIBRARY_PATH", explicit, None if Path(explicit).exists() else "file not found"
        )
    if system == "Darwin":
        for kind, path in MACOS_CANDIDATES:
            if Path(path).exists():
                problem = None
                if kind.startswith("macFUSE") and not MACFUSE_BUNDLE.exists():
                    problem = f"{MACFUSE_BUNDLE} is missing; macFUSE is not fully installed"
                return FuseLibrary(kind, path, problem)
        found = ctypes.util.find_library("fuse")
        return FuseLibrary("macFUSE", found) if found else None
    if system == "Windows":
        dll = _windows_winfsp()
        return FuseLibrary("WinFsp", dll) if dll else None
    for name, kind in (("fuse3", "libfuse3"), ("fuse", "libfuse2")):
        found = ctypes.util.find_library(name)
        if found:
            return FuseLibrary(kind, found)
    return None


_module: ModuleType | None = None


def ensure_loaded() -> ModuleType:
    """Import and return `mfusepy`, pointing it at the library found by `find_library()`."""
    global _module
    if _module is not None:
        return _module
    lib = find_library()
    if lib is None:
        raise FuseNotFoundError(INSTALL_HINTS.get(platform.system(), "No FUSE library found."))
    os.environ["FUSE_LIBRARY_PATH"] = lib.path
    try:
        import mfusepy
    except (OSError, ImportError) as e:
        raise FuseNotFoundError(f"Could not load FUSE library {lib.path}: {e}") from e
    module: ModuleType = mfusepy
    _module = module
    return module


def hint() -> str:
    return INSTALL_HINTS.get(platform.system(), "")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
