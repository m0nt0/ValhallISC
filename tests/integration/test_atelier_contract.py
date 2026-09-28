"""Behaviour every AtelierApi implementation (real client and test fake) must share."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import defusedxml.ElementTree as ET
import pytest

from irisfs.atelier.api import AtelierApi
from irisfs.atelier.errors import AtelierError, NotFoundError
from tests.integration.xmlsamples import class_xml, routine_xml

PKG = "Irisfs.Contract"


@contextlib.contextmanager
def cleanup(api: AtelierApi, ns: str, *names: str) -> Iterator[None]:
    try:
        yield
    finally:
        for n in names:
            with contextlib.suppress(AtelierError):
                api.delete_doc(ns, n)


def test_server_info(api: AtelierApi) -> None:
    info = api.server_info()
    assert info.api >= 7
    assert "USER" in info.namespaces and "USER" in api.namespaces()


def test_namespace_info(api: AtelierApi) -> None:
    assert api.namespace_info("USER").default_db
    assert api.namespace_info("%SYS").default_db  # '%' must be URL-encoded


def test_import_list_export_delete_class(api: AtelierApi, unique: str) -> None:
    cls = f"{PKG}.{unique}"
    doc = f"{cls}.cls"
    with cleanup(api, "USER", doc):
        res = api.import_xml("USER", class_xml(cls), file="t.xml", flags="ck")
        assert res.ok, res.error
        assert res.imported == (doc,)

        listed = {d.name: d for d in api.list_docs("USER")}
        assert doc in listed
        assert listed[doc].cat == "CLS" and listed[doc].ts and listed[doc].db
        assert api.doc_timestamp("USER", doc)

        root = ET.fromstring(api.export_xml("USER", doc))
        assert root.tag == "Export"
        assert [el.get("name") for el in root.findall("Class")] == [cls]

        api.import_xml("USER", class_xml(cls, body='quit "v2"'))
        assert b'quit "v2"' in api.export_xml("USER", doc)

        api.delete_doc("USER", doc)
        with pytest.raises(NotFoundError):
            api.export_xml("USER", doc)


def test_list_folder_one_level_at_a_time(api: AtelierApi, unique: str) -> None:
    cls = f"{PKG}.{unique}.Deep.Leaf"
    doc = f"{cls}.cls"
    with cleanup(api, "USER", doc):
        assert api.import_xml("USER", class_xml(cls)).ok

        def listing(package: str, **kw: bool) -> dict[str, bool]:
            flags = {"system": False, "generated": False, "mapped": True, **kw}
            return {e.name: e.is_dir for e in api.list_folder("USER", package, **flags)}

        assert listing("")["Irisfs"] is True
        assert listing("Irisfs")["Contract"] is True
        assert listing(f"{PKG}")[unique] is True
        assert listing(f"{PKG}.{unique}") == {"Deep": True}
        leaf = [
            e
            for e in api.list_folder(
                "USER", f"{PKG}.{unique}.Deep", system=False, generated=False, mapped=True
            )
        ]
        assert [(e.name, e.is_dir) for e in leaf] == [(doc, False)]
        assert leaf[0].ts
        assert listing(f"{PKG}.{unique}.Deep", mapped=False) == {doc: False}  # in USER's own database
        # %-packages only on request ("other" documents such as %*.LUT are listed anyway: irisfs filters them)
        assert not any(name.startswith("%") for name, is_dir in listing("").items() if is_dir)
        assert [d.name for d in api.list_docs("USER", like=f"{PKG}.{unique}.%")] == [doc]
        assert api.first_class("USER", f"{PKG}.{unique}") == cls
        assert api.first_class("USER", f"{PKG}.{unique}.Nope") is None
        first = api.list_folder("USER", "Irisfs", system=False, generated=False, mapped=True, limit=1)
        assert [e.name for e in first] == ["Contract"]


def test_import_routine_name_normalized(api: AtelierApi, unique: str) -> None:
    rtn = "IRISFS" + unique.upper()
    with cleanup(api, "USER", f"{rtn}.mac"):
        res = api.import_xml("USER", routine_xml(rtn))
        assert res.imported == (f"{rtn}.mac",), res
        assert f"{rtn}.mac" in {d.name for d in api.list_docs("USER", category="RTN")}


def test_compile_error_imports_but_reports(api: AtelierApi, unique: str) -> None:
    cls = f"{PKG}.{unique}"
    with cleanup(api, "USER", f"{cls}.cls"):
        res = api.import_xml("USER", class_xml(cls, body="quit 1 +++ )"), flags="ck")
        assert res.imported == (f"{cls}.cls",)
        assert not res.ok


def test_no_compile_flag_imports_cleanly(api: AtelierApi, unique: str) -> None:
    cls = f"{PKG}.{unique}"
    with cleanup(api, "USER", f"{cls}.cls"):
        res = api.import_xml("USER", class_xml(cls, body="quit 1 +++ )"), flags="-c")
        assert res.imported == (f"{cls}.cls",) and res.ok


@pytest.mark.parametrize(
    "data",
    [b"<Export><Class name='Broken'>", b"<foo/>", b"not xml at all"],
    ids=["malformed", "not-an-export", "garbage"],
)
def test_bad_xml_is_rejected_with_nothing_imported(api: AtelierApi, data: bytes) -> None:
    before = {d.name for d in api.list_docs("USER")}
    res = api.import_xml("USER", data)
    assert res.imported == () and not res.ok
    assert {d.name for d in api.list_docs("USER")} == before


def test_missing_things(api: AtelierApi) -> None:
    with pytest.raises(NotFoundError):
        api.export_xml("USER", f"{PKG}.DoesNotExist.cls")
    with pytest.raises(NotFoundError):
        api.delete_doc("USER", f"{PKG}.DoesNotExist.cls")
    with pytest.raises(NotFoundError):
        api.list_docs("NOSUCHNS")


def test_unicode_round_trip(api: AtelierApi, unique: str) -> None:
    cls = f"{PKG}.{unique}"
    text = "àèìòù € 漢字 😀"
    with cleanup(api, "USER", f"{cls}.cls"):
        api.import_xml("USER", class_xml(cls, description=text))
        assert text.encode("utf-8") in api.export_xml("USER", f"{cls}.cls")


def test_no_source_control_in_the_test_namespace(api: AtelierApi) -> None:
    # USER has no source control class (the e2e test registers one only for its own duration)
    assert api.source_control_enabled("USER") is False
    assert api.source_control_status("USER", ["Demo.Person.cls"]) == {}
