"""Phase 5 end-to-end: read path through a real FUSE mount, exercised with real tools."""

from __future__ import annotations

import datetime as dt
import errno
import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import defusedxml.ElementTree as ET
import pytest

from irisfs.atelier.client import AtelierClient
from irisfs.mount.unmount import is_mounted
from tests.conftest import IrisConn
from tests.e2e.conftest import MountedFs, TcpProxy

pytestmark = [pytest.mark.iris, pytest.mark.fuse]
TS = re.compile(rb'\s+ts="[^"]*"')


@pytest.fixture(scope="module")
def client(iris_conn: IrisConn) -> AtelierClient:
    return AtelierClient(iris_conn.base_url, iris_conn.user, iris_conn.password)


def reference(client: AtelierClient, ns: str, name: str) -> bytes:
    return TS.sub(b"", client.export_xml(ns, name))


def test_e2e01_root_lists_namespaces(mnt: MountedFs) -> None:
    assert {"USER", "TESTNS", "%SYS"} <= set(os.listdir(mnt.path))


def test_e2e02_package_listing(mnt: MountedFs) -> None:
    listing = set(os.listdir(mnt.path / "USER" / "Demo"))
    assert {
        "Person.cls.xml",
        "Util.cls.xml",
        "Unicode.cls.xml",
        "Big.cls.xml",
        "Sub",
        "Rtn2.mac.xml",
    } <= listing
    assert (mnt.path / "USER" / "Demo" / "Sub").is_dir()
    user = set(os.listdir(mnt.path / "USER"))
    assert {"DEMORTN.mac.xml", "DemoInc.inc.xml", "DemoTable.LUT.xml"} <= user
    assert not any(n.startswith(("Ens", "%", "CSPX", "INFORMATION")) for n in user)


def test_e2e03_cat_is_iris_export(mnt: MountedFs) -> None:
    out = subprocess.run(
        ["cat", str(mnt.path / "USER/Demo/Person.cls.xml")], capture_output=True, check=True
    ).stdout
    root = ET.fromstring(out)
    assert root.tag == "Export" and root.find("Class").get("name") == "Demo.Person"  # type: ignore[union-attr]


def test_e2e04_cp_out_matches_export(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    subprocess.run(["cp", str(mnt.path / "USER/Demo/Person.cls.xml"), str(tmp_path / "p.xml")], check=True)
    assert TS.sub(b"", (tmp_path / "p.xml").read_bytes()) == reference(client, "USER", "Demo.Person.cls")


def test_e2e05_unicode_bytes(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    shutil.copyfile(mnt.path / "USER/Demo/Unicode.cls.xml", tmp_path / "u.xml")
    data = (tmp_path / "u.xml").read_bytes()
    assert TS.sub(b"", data) == reference(client, "USER", "Demo.Unicode.cls")
    assert "漢字 😀".encode() in data


def test_e2e06_big_file_twice(mnt: MountedFs, client: AtelierClient, tmp_path: Path) -> None:
    want = hashlib.sha256(reference(client, "USER", "Demo.Big.cls")).hexdigest()
    for i in range(2):
        subprocess.run(
            ["cp", str(mnt.path / "USER/Demo/Big.cls.xml"), str(tmp_path / f"b{i}.xml")], check=True
        )
        got = (tmp_path / f"b{i}.xml").read_bytes()
        assert len(got) > 1_000_000
        assert hashlib.sha256(TS.sub(b"", got)).hexdigest() == want


def test_e2e07_stat(mnt: MountedFs, client: AtelierClient) -> None:
    st = os.stat(mnt.path / "USER/Demo/Person.cls.xml")
    data = (mnt.path / "USER/Demo/Person.cls.xml").read_bytes()
    assert st.st_size == len(data)
    ts = next(d.ts for d in client.list_docs("USER") if d.name == "Demo.Person.cls")
    expected = dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    assert abs(st.st_mtime - expected) < 2


def test_e2e08_recursive_copy(mnt: MountedFs, tmp_path: Path) -> None:
    subprocess.run(["cp", "-R", str(mnt.path / "TESTNS"), str(tmp_path / "copy")], check=True)
    copied = {
        p.relative_to(tmp_path / "copy").as_posix() for p in (tmp_path / "copy").rglob("*") if p.is_file()
    }
    # TESTNS holds exactly these user documents; library items are hidden by the default filter
    assert copied == {"Test/A.cls.xml", "Test/B.cls.xml", "TESTRTN.mac.xml"}
    for rel in copied:
        assert (tmp_path / "copy" / rel).read_bytes().startswith(b"<?xml")


def test_e2e13_stop_unmounts_cleanly(mount_factory: Callable[..., MountedFs]) -> None:
    m = mount_factory()
    started = time.monotonic()
    assert m.stop() == 0
    assert time.monotonic() - started < 10
    assert not is_mounted(str(m.path))
    assert any(e.get("event") == "unmounted" for e in m.events)


def test_e2e14_server_outage_eio_then_recovers(
    mount_factory: Callable[..., MountedFs], proxy: TcpProxy
) -> None:
    m = mount_factory(host="127.0.0.1", port=proxy.port)
    assert "USER" in os.listdir(m.path)
    proxy.down()
    started = time.monotonic()
    with pytest.raises(OSError) as exc:
        (m.path / "TESTNS/Test/B.cls.xml").read_bytes()  # not cached yet
    assert exc.value.errno in (errno.EIO, errno.ENOENT)
    assert time.monotonic() - started < 15
    proxy.up()
    deadline = time.monotonic() + 15
    while True:
        try:
            assert b"Test.B" in (m.path / "TESTNS/Test/B.cls.xml").read_bytes()
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    assert is_mounted(str(m.path))


def test_e2e15_wrong_password(mount_factory: Callable[..., MountedFs]) -> None:
    m = mount_factory(password="wrong", expect_mount=False)
    event = m.wait_for(lambda e: e.get("event") == "error", timeout=30)
    assert event["code"] == "AUTH_FAILED"
    assert m.proc.wait(15) != 0
    assert not is_mounted(str(m.path))


def test_e2e16_parallel_reads(mnt: MountedFs) -> None:
    paths = [
        mnt.path / p
        for p in (
            "USER/Demo/Person.cls.xml",
            "USER/Demo/Util.cls.xml",
            "USER/DEMORTN.mac.xml",
            "USER/DemoInc.inc.xml",
            "TESTNS/Test/A.cls.xml",
        )
    ] * 10
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda p: subprocess.run(["cat", str(p)], capture_output=True), paths))
    assert all(r.returncode == 0 and r.stdout.startswith(b"<?xml") for r in results)


def test_e2e19_nonempty_mountpoint_rejected(mount_factory: Callable[..., MountedFs], tmp_path: Path) -> None:
    (tmp_path / "somefile").write_text("x")
    m = mount_factory(mount_point=str(tmp_path), expect_mount=False)
    event = m.wait_for(lambda e: e.get("event") == "error", timeout=30)
    assert event["code"] == "MOUNTPOINT_INVALID"
    assert (tmp_path / "somefile").exists()
