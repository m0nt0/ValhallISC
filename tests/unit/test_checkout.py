"""Source control state shown in file managers (ADR-017): tags, read-only files, _CHECKED_OUT.txt."""

from __future__ import annotations

import errno
import os
import plistlib
from typing import Any

import pytest

from irisfs.atelier.models import SourceStatus
from irisfs.vfs import checkout
from irisfs.vfs.errors import FsError
from irisfs.vfs.vfs import Options, VirtualFS
from tests.fakes.fake_atelier import FakeAtelier

MINE = SourceStatus(True, True, True, "dev")
THEIRS = SourceStatus(True, False, True, "mario")
LOCKED = SourceStatus(True, False, False, "")


def make_fake(with_sc: bool = True) -> FakeAtelier:
    f = FakeAtelier()
    for name in ("Demo.Mine.cls", "Demo.Theirs.cls", "Demo.Locked.cls", "Demo.Plain.cls", "Other.X.cls"):
        f.add_doc("USER", name)
    if with_sc:
        f.source_control["USER"] = {
            "Demo.Mine.cls": MINE,
            "Demo.Theirs.cls": THEIRS,
            "Demo.Locked.cls": LOCKED,
        }
    return f


def fs(fake: FakeAtelier, **opts: Any) -> VirtualFS:
    base: dict[str, Any] = {"prefetch_workers": 0, "background_refresh": False, "user": "DEV"}
    return VirtualFS(fake, Options(**{**base, **opts}))


def tags(v: VirtualFS, path: str) -> list[str]:
    return list(plistlib.loads(v.getxattr(path, checkout.MAC_TAGS)))


def no_attr(v: VirtualFS, path: str, name: str) -> bool:
    with pytest.raises(FsError) as exc:
        v.getxattr(path, name)
    return exc.value.errno in (errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA))


def test_finder_tags_green_for_me_orange_for_others() -> None:
    v = fs(make_fake(), checkout_marks="macos")
    v.readdir("/USER/Demo")
    assert tags(v, "/USER/Demo/Mine.cls.xml") == ["Checked out\n2"]  # user names match case-insensitively
    assert tags(v, "/USER/Demo/Theirs.cls.xml") == ["Checked out by mario\n7"]
    assert no_attr(v, "/USER/Demo/Plain.cls.xml", checkout.MAC_TAGS)
    assert v.listxattr("/USER/Demo/Mine.cls.xml") == [checkout.MAC_TAGS]
    assert v.listxattr("/USER/Demo/Plain.cls.xml") == []


def test_xdg_attributes_for_linux() -> None:
    v = fs(make_fake(), checkout_marks="xdg")
    v.readdir("/USER/Demo")
    assert v.getxattr("/USER/Demo/Theirs.cls.xml", checkout.XDG_TAGS) == b"Checked out by mario"
    assert v.getxattr("/USER/Demo/Mine.cls.xml", checkout.XDG_COMMENT) == b"Checked out"
    assert no_attr(v, "/USER/Demo/Mine.cls.xml", checkout.MAC_TAGS)


def test_not_editable_documents_are_read_only() -> None:
    v = fs(make_fake())
    v.readdir("/USER/Demo")
    assert v.getattr("/USER/Demo/Locked.cls.xml").mode == 0o444
    assert v.getattr("/USER/Demo/Theirs.cls.xml").mode == 0o444  # checked out by someone else
    assert v.getattr("/USER/Demo/Mine.cls.xml").mode == 0o644
    assert v.getattr("/USER/Demo/Plain.cls.xml").mode == 0o644


def test_write_refused_only_if_still_not_editable_when_asked_again() -> None:
    fake = make_fake()
    v = fs(fake)
    v.readdir("/USER/Demo")
    with pytest.raises(FsError) as exc:
        v.open("/USER/Demo/Locked.cls.xml", os.O_WRONLY | os.O_TRUNC)
    assert exc.value.errno == errno.EACCES
    fake.source_control["USER"]["Demo.Locked.cls"] = MINE  # checked out meanwhile, e.g. in VS Code
    fh = v.open("/USER/Demo/Locked.cls.xml", os.O_WRONLY | os.O_TRUNC)
    v.release("/USER/Demo/Locked.cls.xml", fh)


def test_list_file_where_something_is_checked_out() -> None:
    v = fs(make_fake(), checkout_list_file=True)
    assert checkout.LIST_FILE in v.readdir("/USER/Demo")
    assert checkout.LIST_FILE not in v.readdir("/USER/Other")
    path = f"/USER/Demo/{checkout.LIST_FILE}"
    attr = v.getattr(path)
    assert attr.mode == 0o444
    fh = v.open(path, os.O_RDONLY)
    text = v.read(path, attr.size, 0, fh).decode()
    assert "Mine.cls.xml\tchecked out by you" in text
    assert "Theirs.cls.xml\tchecked out by mario" in text
    assert "Plain" not in text and "Locked" not in text
    with pytest.raises(FsError):
        v.open(path, os.O_WRONLY)
    with pytest.raises(FsError):
        v.getattr(f"/USER/Other/{checkout.LIST_FILE}")


def test_namespace_without_source_control_costs_one_question() -> None:
    fake = make_fake(with_sc=False)
    v = fs(fake, checkout_marks="macos", checkout_list_file=True)
    assert checkout.LIST_FILE not in v.readdir("/USER/Demo")
    v.readdir("/USER/Other")
    assert v.listxattr("/USER/Demo/Mine.cls.xml") == []
    assert fake.calls["source_control_enabled"] == 1 and fake.calls["source_control_status"] == 0


def test_one_status_query_per_folder_listing() -> None:
    fake = make_fake()
    v = fs(fake, checkout_marks="macos")
    v.readdir("/USER/Demo")
    tags(v, "/USER/Demo/Mine.cls.xml")
    tags(v, "/USER/Demo/Theirs.cls.xml")
    assert no_attr(v, "/USER/Demo/Plain.cls.xml", checkout.MAC_TAGS)
    assert fake.calls["source_control_status"] == 1


def test_a_checkout_moves_the_folder_mtime() -> None:
    fake = make_fake()
    now = [0.0]
    v = VirtualFS(
        fake,
        Options(prefetch_workers=0, background_refresh=False, tree_ttl=5, user="dev"),
        clock=lambda: now[0],
        wall_clock=lambda: 1000.0 + now[0],
    )
    v.readdir("/USER/Demo")
    before = v.getattr("/USER/Demo").mtime
    fake.source_control["USER"]["Demo.Plain.cls"] = MINE  # no document changed, only its state
    now[0] = 10
    v.readdir("/USER/Demo")
    assert v.getattr("/USER/Demo").mtime > before
