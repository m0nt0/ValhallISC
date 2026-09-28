"""Asking the running instance to show its window (irisfs.gui.instance, ADR-016)."""

from __future__ import annotations

from pathlib import Path

from irisfs.gui import instance


def test_request_is_taken_once(tmp_path: Path) -> None:
    assert not instance.take_show_request(tmp_path)
    instance.request_show(tmp_path)
    assert instance.take_show_request(tmp_path)
    assert not instance.take_show_request(tmp_path)


def test_request_creates_the_settings_folder(tmp_path: Path) -> None:
    folder = tmp_path / "not-yet"
    instance.request_show(folder)
    assert instance.take_show_request(folder)
