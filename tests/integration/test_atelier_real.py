"""Checks that only make sense against the real IRIS container."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import defusedxml.ElementTree as ET
import pytest

from irisfs.atelier.client import AtelierClient
from irisfs.atelier.errors import AuthError, ConnectionFailed, DeployedError
from tests.conftest import IrisConn
from tests.integration.xmlsamples import class_xml

pytestmark = pytest.mark.iris

SEED_USER = {
    "Demo.Person.cls",
    "Demo.Util.cls",
    "Demo.Unicode.cls",
    "Demo.Sub.Thing.cls",
    "Demo.Big.cls",
    "DEMORTN.mac",
    "Demo.Rtn2.mac",
    "DemoInc.inc",
    "DemoTable.LUT",
}


def test_seed_items_listed_with_categories(real_client: AtelierClient) -> None:
    docs = {d.name: d for d in real_client.list_docs("USER")}
    assert set(docs) >= SEED_USER
    assert docs["Demo.Person.cls"].cat == "CLS"
    assert docs["DEMORTN.mac"].cat == "RTN"
    assert docs["DemoTable.LUT"].cat == "OTH"
    assert {"Test.A.cls", "Test.B.cls", "TESTRTN.mac"} <= {d.name for d in real_client.list_docs("TESTNS")}


def test_deployed_class_export_is_refused(real_client: AtelierClient) -> None:
    # %Library.SQLCatalogPriv ships in deployed mode (no source): IRIS answers #6309
    with pytest.raises(DeployedError):
        real_client.export_xml("%SYS", "%Library.SQLCatalogPriv.cls")


def test_namespaces(real_client: AtelierClient) -> None:
    assert {"USER", "TESTNS", "%SYS"} <= set(real_client.namespaces())


@pytest.mark.parametrize("name", sorted(SEED_USER))
def test_every_seed_item_exports_as_iris_xml(real_client: AtelierClient, name: str) -> None:
    root = ET.fromstring(real_client.export_xml("USER", name))
    assert root.tag == "Export" and root.get("generator") == "IRIS"
    assert len(list(root)) >= 1


def test_unicode_seed_class(real_client: AtelierClient) -> None:
    data = real_client.export_xml("USER", "Demo.Unicode.cls")
    for text in ("àèìòù", "€", "漢字", "😀"):
        assert text.encode("utf-8") in data


def test_big_class_exports_fully(real_client: AtelierClient) -> None:
    data = real_client.export_xml("USER", "Demo.Big.cls")
    assert len(data) > 1_000_000
    assert data.rstrip().endswith(b"</Export>")


def test_wrong_password_fails_fast(iris_conn: IrisConn) -> None:
    started = time.perf_counter()
    with AtelierClient(iris_conn.base_url, iris_conn.user, "wrong-password") as c, pytest.raises(AuthError):
        c.server_info()
    assert time.perf_counter() - started < 3


def test_unreachable_host_fails_within_timeout() -> None:
    started = time.perf_counter()
    with AtelierClient("http://127.0.0.1:9", "u", "p", timeout=3) as c, pytest.raises(ConnectionFailed):
        c.server_info()
    assert time.perf_counter() - started < 10


def test_read_only_user_can_export_but_not_import(iris_conn: IrisConn) -> None:
    with AtelierClient(iris_conn.base_url, iris_conn.ro_user, iris_conn.ro_password) as ro:
        assert b"Demo.Person" in ro.export_xml("USER", "Demo.Person.cls")
        res = ro.import_xml("USER", class_xml("Irisfs.Contract.RoDenied"))
        assert res.imported == () and res.error and "5883" in res.error


def test_many_sequential_and_parallel_calls(real_client: AtelierClient) -> None:
    for _ in range(300):
        real_client.doc_timestamp("USER", "Demo.Person.cls")
    names = sorted(SEED_USER - {"Demo.Big.cls"}) * 7  # ~56 exports, 4 concurrent at the client
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda n: real_client.export_xml("USER", n), names))
    assert all(r.startswith(b"<?xml") for r in results)
