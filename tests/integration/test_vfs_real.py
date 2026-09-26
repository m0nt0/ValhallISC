"""VirtualFS with the real Atelier client against the IRIS test container."""

from __future__ import annotations

import contextlib
import errno
import os
from collections.abc import Iterator
from typing import Any

import pytest

from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AtelierError
from irisfs.vfs.errors import FsError
from irisfs.vfs.vfs import Options, VirtualFS
from tests.integration.xmlsamples import class_xml

pytestmark = pytest.mark.iris


@pytest.fixture
def events() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def fs(real_client: AtelierClient, events: list[dict[str, Any]]) -> VirtualFS:
    return VirtualFS(real_client, Options(), on_event=events.append)


def read_all(fs: VirtualFS, path: str) -> bytes:
    fh = fs.open(path, os.O_RDONLY)
    try:
        return fs.read(path, 50_000_000, 0, fh)
    finally:
        fs.release(path, fh)


def write_file(fs: VirtualFS, path: str, data: bytes, *, create: bool = True) -> None:
    fh = fs.create(path, 0o644) if create else fs.open(path, os.O_WRONLY | os.O_TRUNC)
    fs.write(path, data, 0, fh)
    try:
        fs.flush(path, fh)
    finally:
        fs.release(path, fh)


@contextlib.contextmanager
def deleted_after(client: AtelierClient, ns: str, *names: str) -> Iterator[None]:
    try:
        yield
    finally:
        for n in names:
            with contextlib.suppress(AtelierError):
                client.delete_doc(ns, n)


def test_default_view_hides_library_items(fs: VirtualFS) -> None:
    assert {"USER", "TESTNS", "%SYS"} <= set(fs.readdir("/"))
    user = set(fs.readdir("/USER"))
    assert {"Demo", "DEMORTN.mac.xml", "DemoInc.inc.xml", "DemoTable.LUT.xml"} <= user
    assert not {"CSPX", "INFORMATION", "%Library", "HIPAA_4010.X12.xml", "EnsDisplayString.mac.xml"} & user
    assert set(fs.readdir("/USER/Demo")) >= {
        "Person.cls.xml",
        "Util.cls.xml",
        "Unicode.cls.xml",
        "Big.cls.xml",
        "Rtn2.mac.xml",
        "Sub",
    }
    assert set(fs.readdir("/TESTNS")) >= {"Test", "TESTRTN.mac.xml"}


def test_percent_sys_is_browsable(fs: VirtualFS) -> None:
    assert fs.getattr("/%SYS").is_dir
    fs.readdir("/%SYS")


@pytest.mark.parametrize(
    "path",
    [
        "/USER/Demo/Person.cls.xml",
        "/USER/Demo/Util.cls.xml",
        "/USER/Demo/Unicode.cls.xml",
        "/USER/Demo/Sub/Thing.cls.xml",
        "/USER/Demo/Big.cls.xml",
        "/USER/DEMORTN.mac.xml",
        "/USER/Demo/Rtn2.mac.xml",
        "/USER/DemoInc.inc.xml",
        "/USER/DemoTable.LUT.xml",
        "/TESTNS/Test/A.cls.xml",
    ],
)
def test_every_seed_item_reads_with_consistent_size(fs: VirtualFS, path: str) -> None:
    data = read_all(fs, path)
    assert data.startswith(b"<?xml") and b"<Export" in data
    assert fs.getattr(path).size == len(data)


def test_modify_existing_class_and_restore(
    fs: VirtualFS, real_client: AtelierClient, events: list[Any]
) -> None:
    path = "/USER/Demo/Util.cls.xml"
    original = read_all(fs, path)
    try:
        modified = original.replace(b"Utility class methods", b"Utility class methods (irisfs)")
        assert modified != original
        write_file(fs, path, modified, create=False)
        assert events[-1]["event"] == "imported" and events[-1]["items"] == ["Demo.Util.cls"]
        assert b"(irisfs)" in read_all(fs, path)
    finally:
        write_file(fs, path, original, create=False)
    assert b"(irisfs)" not in real_client.export_xml("USER", "Demo.Util.cls")


def test_new_class_appears(fs: VirtualFS, real_client: AtelierClient, events: list[Any]) -> None:
    with deleted_after(real_client, "USER", "Demo.IrisfsVfs.cls"):
        write_file(fs, "/USER/Demo/IrisfsVfs.cls.xml", class_xml("Demo.IrisfsVfs"))
        assert events[-1]["event"] == "imported" and events[-1]["compile_errors"] == []
        assert "IrisfsVfs.cls.xml" in fs.readdir("/USER/Demo")


def test_malformed_xml_changes_nothing(fs: VirtualFS, real_client: AtelierClient, events: list[Any]) -> None:
    before = {d.name for d in real_client.list_docs("USER")}
    with pytest.raises(FsError) as exc:
        write_file(fs, "/USER/broken.xml", b"<Export><Class name='Demo.Broken'>")
    assert exc.value.errno == errno.EIO
    assert events[-1]["event"] == "import_failed"
    assert {d.name for d in real_client.list_docs("USER")} == before
