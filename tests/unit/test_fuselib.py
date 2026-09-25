from pathlib import Path

import pytest

from irisfs.mount import fuselib


def test_explicit_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lib = tmp_path / "libfuse.so"
    lib.write_bytes(b"")
    monkeypatch.setenv("FUSE_LIBRARY_PATH", str(lib))
    found = fuselib.find_library("Linux")
    assert found is not None and found.path == str(lib) and found.problem is None


def test_explicit_env_missing_file_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUSE_LIBRARY_PATH", "/nonexistent/libfuse.so")
    found = fuselib.find_library("Linux")
    assert found is not None and found.problem == "file not found"


def test_macos_search_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUSE_LIBRARY_PATH", raising=False)
    present = {
        "/opt/local/lib/libfuse.2.dylib",
        "/opt/homebrew/lib/libfuse.2.dylib",
        str(fuselib.MACFUSE_BUNDLE),
    }
    monkeypatch.setattr(fuselib.Path, "exists", lambda self: str(self) in present)
    found = fuselib.find_library("Darwin")
    assert found is not None
    assert found.path == "/opt/local/lib/libfuse.2.dylib" and found.problem is None


def test_macos_fuse_t_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUSE_LIBRARY_PATH", raising=False)
    present = {"/usr/local/lib/libfuse-t.dylib", "/opt/local/lib/libfuse.2.dylib"}
    monkeypatch.setattr(fuselib.Path, "exists", lambda self: str(self) in present)
    found = fuselib.find_library("Darwin")
    assert found is not None and found.kind == "FUSE-T"


def test_macfuse_without_bundle_flags_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUSE_LIBRARY_PATH", raising=False)
    present = {"/usr/local/lib/libfuse.2.dylib"}
    monkeypatch.setattr(fuselib.Path, "exists", lambda self: str(self) in present)
    found = fuselib.find_library("Darwin")
    assert found is not None and found.problem and "macfuse.fs" in found.problem


def test_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUSE_LIBRARY_PATH", raising=False)
    monkeypatch.setattr(fuselib.Path, "exists", lambda self: False)
    monkeypatch.setattr(fuselib.ctypes.util, "find_library", lambda name: None)
    assert fuselib.find_library("Darwin") is None
    assert fuselib.find_library("Linux") is None


def test_linux_prefers_fuse3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FUSE_LIBRARY_PATH", raising=False)
    monkeypatch.setattr(fuselib.ctypes.util, "find_library", lambda n: f"lib{n}.so")
    found = fuselib.find_library("Linux")
    assert found is not None and found.kind == "libfuse3"
