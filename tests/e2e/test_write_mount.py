"""Phase 6 end-to-end: importing by copying XML files into a real FUSE mount."""

from __future__ import annotations

import contextlib
import http.server
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AtelierError
from tests.conftest import IrisConn
from tests.e2e.conftest import MountedFs
from tests.integration.xmlsamples import HEADER, class_xml, routine_xml

pytestmark = [pytest.mark.iris, pytest.mark.fuse]
TS = re.compile(rb'\s+ts="[^"]*"')


@pytest.fixture
def client(iris_conn: IrisConn) -> Iterator[AtelierClient]:
    with AtelierClient(iris_conn.base_url, iris_conn.user, iris_conn.password) as c:
        yield c


@pytest.fixture
def cleanup(client: AtelierClient) -> Iterator[Callable[[str, str], None]]:
    """Register (ns, doc) to delete after the test."""
    todo: list[tuple[str, str]] = []
    yield lambda ns, doc: todo.append((ns, doc))
    for ns, doc in todo:
        with contextlib.suppress(AtelierError):
            client.delete_doc(ns, doc)


def uniq() -> str:
    return "E" + uuid.uuid4().hex[:8]


def cp(src: Path, dst: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["cp", *flags, str(src), str(dst)], capture_output=True, text=True, timeout=60)


def wait_import(m: MountedFs, after: int, filename: str, timeout: float = 30) -> dict[str, Any]:
    """Next imported/import_failed event for `filename` (import runs on FUSE release, after cp returns)."""
    deadline = time.monotonic() + timeout
    index = after
    while True:
        event = m.next_event(index, timeout=max(0.1, deadline - time.monotonic()))
        index += 1
        if event.get("event") in ("imported", "import_failed") and event.get("file") == filename:
            return event


def doc_names(client: AtelierClient, ns: str) -> set[str]:
    return {d.name for d in client.list_docs(ns)}


def test_e2e20_modify_existing_class(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    target = mnt.path / "USER/Demo/Util.cls.xml"
    original = client.export_xml("USER", "Demo.Util.cls")
    try:
        local = tmp_path / "Util.cls.xml"
        shutil.copyfile(target, local)
        text = local.read_text(encoding="utf-8")
        method = (
            '<Method name="IrisfsAdded">\n<ClassMethod>1</ClassMethod>\n<ReturnType>%String</ReturnType>\n'
            '<Implementation><![CDATA[\n    quit "added"\n]]></Implementation>\n</Method>\n'
        )
        local.write_text(text.replace("</Class>", method + "</Class>", 1), encoding="utf-8")
        n = mnt.event_count()
        r = cp(local, target)
        assert r.returncode == 0, r.stderr
        event = wait_import(mnt, n, "Util.cls.xml")
        assert (
            event["event"] == "imported"
            and event["items"] == ["Demo.Util.cls"]
            and event["compile_errors"] == []
        )
        assert b'name="IrisfsAdded"' in client.export_xml("USER", "Demo.Util.cls")
        assert b"IrisfsAdded" in target.read_bytes()  # the mount shows the new version
    finally:
        client.import_xml("USER", original)
    assert b"IrisfsAdded" not in client.export_xml("USER", "Demo.Util.cls")


def test_e2e21_new_class_with_other_filename(
    mnt: MountedFs, client: AtelierClient, tmp_path: Path, cleanup: Callable[[str, str], None]
) -> None:
    name = f"Demo.{uniq()}"
    cleanup("USER", f"{name}.cls")
    src = tmp_path / "newclass.xml"
    src.write_bytes(class_xml(name))
    n = mnt.event_count()
    r = cp(src, mnt.path / "USER")
    assert r.returncode == 0, r.stderr
    event = wait_import(mnt, n, "newclass.xml")
    assert event["event"] == "imported" and event["items"] == [f"{name}.cls"]
    assert f"{name}.cls" in doc_names(client, "USER")
    assert (mnt.path / "USER" / "Demo" / f"{name.split('.')[1]}.cls.xml").exists()
    assert (mnt.path / "USER" / "newclass.xml").exists()  # ghost entry until its TTL expires


def test_e2e22_non_xml_refused(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    before = doc_names(client, "USER")
    src = tmp_path / "notes.txt"
    src.write_text("hello")
    r = cp(src, mnt.path / "USER")
    assert r.returncode != 0 and "denied" in r.stderr.lower()
    assert not (mnt.path / "USER" / "notes.txt").exists()
    assert doc_names(client, "USER") == before


def test_e2e23_malformed_xml(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    before = doc_names(client, "USER")
    src = tmp_path / "broken.xml"
    src.write_bytes(b"<Export generator='IRIS'><Class name='Demo.Broken'>")
    n = mnt.event_count()
    r = cp(src, mnt.path / "USER")
    event = wait_import(mnt, n, "broken.xml")
    assert event["event"] == "import_failed"
    # Linux reports the flush error to cp; either way nothing must reach IRIS.
    if platform.system() == "Linux":
        assert r.returncode != 0
    assert doc_names(client, "USER") == before


def test_e2e24_read_only_mount(
    mount_factory: Callable[..., MountedFs], client: AtelierClient, tmp_path: Path
) -> None:
    m = mount_factory(read_only=True)
    before = doc_names(client, "USER")
    src = tmp_path / "ro.xml"
    src.write_bytes(class_xml("Demo.ReadOnlyAttempt"))
    r = cp(src, m.path / "USER")
    assert r.returncode != 0 and "read-only" in r.stderr.lower()
    r = cp(src, m.path / "USER/Demo/Person.cls.xml")
    assert r.returncode != 0
    assert (m.path / "USER/Demo/Person.cls.xml").read_bytes().startswith(b"<?xml")  # reads still work
    assert doc_names(client, "USER") == before


def test_e2e25_rm_mv_mkdir_refused(mnt: MountedFs, client: AtelierClient) -> None:
    before = doc_names(client, "USER")
    person = mnt.path / "USER/Demo/Person.cls.xml"
    for cmd in (
        ["rm", str(person)],
        ["mv", str(person), str(mnt.path / "USER/Demo/X.cls.xml")],
        ["mkdir", str(mnt.path / "USER/NewPkg")],
        ["rmdir", str(mnt.path / "USER/Demo/Sub")],
    ):
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode != 0, cmd
    assert person.exists()
    assert doc_names(client, "USER") == before


def test_e2e26_compile_error_reported(
    mnt: MountedFs, tmp_path: Path, cleanup: Callable[[str, str], None]
) -> None:
    name = f"Demo.{uniq()}"
    cleanup("USER", f"{name}.cls")
    src = tmp_path / "bad.xml"
    src.write_bytes(class_xml(name, body="quit 1 +++ )"))
    n = mnt.event_count()
    assert cp(src, mnt.path / "USER").returncode == 0
    event = wait_import(mnt, n, "bad.xml")
    assert event["event"] == "imported" and event["items"] == [f"{name}.cls"]
    assert event["compile_errors"] and "ERROR" in event["compile_errors"][0]


def test_e2e27_multi_item_file(
    mnt: MountedFs, client: AtelierClient, tmp_path: Path, cleanup: Callable[[str, str], None]
) -> None:
    u = uniq()
    cls, rtn, inc = f"Demo.{u}", f"R{u}", f"I{u}"
    for doc in (f"{cls}.cls", f"{rtn}.mac", f"{inc}.inc"):
        cleanup("USER", doc)

    def body(xml: bytes) -> bytes:
        return xml.split(b"\n", 2)[2].rsplit(b"</Export>", 1)[0]

    include = (
        routine_xml(inc).replace(b'type="MAC"', b'type="INC"').replace(f"{inc} ;".encode(), b"#define X 1 ;")
    )
    data = HEADER.encode() + body(class_xml(cls)) + body(routine_xml(rtn)) + body(include) + b"</Export>\n"
    (tmp_path / "bundle.xml").write_bytes(data)
    n = mnt.event_count()
    assert cp(tmp_path / "bundle.xml", mnt.path / "USER").returncode == 0
    event = wait_import(mnt, n, "bundle.xml")
    assert event["event"] == "imported"
    assert sorted(event["items"]) == sorted([f"{cls}.cls", f"{rtn}.mac", f"{inc}.inc"])
    assert {f"{cls}.cls", f"{rtn}.mac", f"{inc}.inc"} <= doc_names(client, "USER")


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Finder/xattr behaviour")
def test_e2e28_macos_metadata(
    mnt: MountedFs, client: AtelierClient, tmp_path: Path, cleanup: Callable[[str, str], None]
) -> None:
    name = f"Demo.{uniq()}"
    cleanup("USER", f"{name}.cls")
    src = tmp_path / "meta.xml"
    src.write_bytes(class_xml(name))
    subprocess.run(["xattr", "-w", "com.example.irisfs", "value", str(src)], check=True)
    n = mnt.event_count()
    for args in (("-X",), ()):  # without and with extended attributes
        r = cp(src, mnt.path / "USER", *args)
        assert r.returncode == 0, r.stderr
        assert wait_import(mnt, n, "meta.xml")["event"] == "imported"
        n = mnt.event_count()
    r = subprocess.run(
        ["ditto", str(src), str(mnt.path / "USER" / "meta.xml")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr
    time.sleep(1)
    assert not any(d.startswith("._") for d in doc_names(client, "USER"))


def test_e2e29_round_trip_restore(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    subprocess.run(["cp", "-R", str(mnt.path / "TESTNS"), str(tmp_path / "backup")], check=True)
    original = client.export_xml("TESTNS", "Test.A.cls")
    client.delete_doc("TESTNS", "Test.A.cls")
    try:
        n = mnt.event_count()
        r = cp(tmp_path / "backup/Test/A.cls.xml", mnt.path / "TESTNS/Test")
        assert r.returncode == 0, r.stderr
        assert wait_import(mnt, n, "A.cls.xml")["event"] == "imported"
        assert TS.sub(b"", client.export_xml("TESTNS", "Test.A.cls")) == TS.sub(b"", original)
    finally:
        if "Test.A.cls" not in doc_names(client, "TESTNS"):
            client.import_xml("TESTNS", original)


def test_e2e30_read_only_user(
    mount_factory: Callable[..., MountedFs], iris_conn: IrisConn, client: AtelierClient, tmp_path: Path
) -> None:
    m = mount_factory(username=iris_conn.ro_user, password=iris_conn.ro_password)
    before = doc_names(client, "USER")
    src = tmp_path / "denied.xml"
    src.write_bytes(class_xml("Demo.IrisfsDenied"))
    n = m.event_count()
    cp(src, m.path / "USER")  # the copy itself may succeed; the import must not
    event = wait_import(m, n, "denied.xml")
    assert event["event"] == "import_failed" and "5883" in event["message"]
    assert doc_names(client, "USER") == before


def test_e2e31_xxe_payload_makes_no_request(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<x/>")

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/xxe"
        payload = (
            f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "{url}">]>'
            '<Export generator="IRIS"><Class name="Demo.Xxe"><Description>&e;</Description></Class></Export>'
        ).encode()
        (tmp_path / "xxe.xml").write_bytes(payload)
        before = doc_names(client, "USER")
        n = mnt.event_count()
        cp(tmp_path / "xxe.xml", mnt.path / "USER")
        event = wait_import(mnt, n, "xxe.xml")
        assert event["event"] == "import_failed" and "unsafe" in event["message"].lower()
        time.sleep(0.5)
        assert hits == []
        assert doc_names(client, "USER") == before
    finally:
        server.shutdown()


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Finder/xattr behaviour")
def test_e2e28b_com_apple_xattrs_accepted(mnt: MountedFs, tmp_path: Path) -> None:
    # Regression: with macFUSE's noapplexattr the kernel refused com.apple.* xattrs (EPERM) and Finder
    # aborted copies with "you don't have permission". Finder sets quarantine/provenance/FinderInfo.
    target = mnt.path / "USER" / "xattr-probe.xml"
    target.touch()  # empty placeholder, like Finder's first step
    try:
        for name in ("com.apple.quarantine", "com.apple.FinderInfo", "com.apple.provenance"):
            # FinderInfo is exactly 32 bytes (-wx = hex); FUSE-T rightly refuses other sizes (ERANGE)
            args = ["-wx", name, "00" * 32] if name == "com.apple.FinderInfo" else ["-w", name, "x"]
            r = subprocess.run(["xattr", *args, str(target)], capture_output=True, text=True)
            assert r.returncode == 0, (name, r.stderr)
    finally:
        target.unlink(missing_ok=True)
