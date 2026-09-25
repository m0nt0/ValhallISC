import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from irisfs.atelier.client import AtelierClient, normalize_doc_name, xml_lines
from irisfs.atelier.errors import (
    AuthError,
    ConnectionFailed,
    ForbiddenError,
    NotFoundError,
    ServerError,
    UnsupportedServer,
)

INFO = {"result": {"content": {"version": "IRIS 2026.1", "api": 8, "namespaces": ["%SYS", "USER"]}}}


def ok(
    content: Any, errors: list[dict[str, Any]] | None = None, console: list[str] | None = None
) -> dict[str, Any]:
    return {
        "status": {"errors": errors or [], "summary": ""},
        "console": console or [],
        "result": {"content": content},
    }


Handler = Callable[[httpx.Request], httpx.Response]


def client_with(
    handler: Handler, *, base: str = "http://iris:52773", prefix: str = "", **kw: Any
) -> AtelierClient:
    """Client whose server-info call succeeds; every other request goes to `handler`."""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/") == prefix + "/api/atelier":
            return httpx.Response(200, json=INFO)
        return handler(request)

    return AtelierClient(base, "_SYSTEM", "Pa55word!", transport=httpx.MockTransport(wrapped), **kw)


# ---- helpers ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DEMORTN.MAC", "DEMORTN.mac"),
        ("X.INC", "X.inc"),
        ("A.B.cls", "A.B.cls"),
        ("T.LUT", "T.LUT"),
        ("noext", "noext"),
    ],
)
def test_normalize_doc_name(raw: str, expected: str) -> None:
    assert normalize_doc_name(raw) == expected


def test_xml_lines_handles_bom_crlf_and_trailing_newline() -> None:
    assert xml_lines("﻿<a>\r\n<b/>\r\n</a>\n".encode()) == ["<a>", "<b/>", "</a>"]
    assert xml_lines(b"<a/>") == ["<a/>"]


# ---- URL building ----------------------------------------------------------------------------
def test_urls_prefix_version_and_namespace_encoding() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=ok([]))

    c = client_with(handler, base="https://srv:8443/iris", prefix="/iris")
    c.list_docs("%SYS")
    assert seen == ["https://srv:8443/iris/api/atelier/v8/%25SYS/docnames/*?generated=0"]


def test_basic_auth_header_sent() -> None:
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=ok([]))

    client_with(handler).list_docs("USER")
    assert headers and headers[0].startswith("Basic ")


# ---- parsing ---------------------------------------------------------------------------------
def test_server_info_and_namespaces() -> None:
    c = client_with(lambda r: httpx.Response(500))
    info = c.server_info()
    assert info.api == 8 and info.namespaces == ("%SYS", "USER")
    assert c.namespaces() == ["%SYS", "USER"]


def test_old_api_rejected() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"content": {"version": "x", "api": 6, "namespaces": []}}})

    c = AtelierClient("http://x", "u", "p", transport=httpx.MockTransport(transport))
    with pytest.raises(UnsupportedServer):
        c.server_info()


def test_not_an_atelier_server() -> None:
    c = AtelierClient(
        "http://x", "u", "p", transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    )
    with pytest.raises(ServerError):
        c.server_info()


def test_list_docs_parses_fields() -> None:
    doc = {
        "name": "Demo.Person.cls",
        "cat": "CLS",
        "ts": "2026-09-25 08:25:21.432",
        "upd": True,
        "db": "USER",
        "gen": False,
    }
    c = client_with(lambda r: httpx.Response(200, json=ok([doc])))
    [d] = c.list_docs("USER")
    assert (d.name, d.cat, d.ts, d.db, d.upd, d.gen) == (
        "Demo.Person.cls",
        "CLS",
        doc["ts"],
        "USER",
        True,
        False,
    )


def test_namespace_info() -> None:
    content = {
        "name": "USER",
        "db": [
            {"name": "USER", "default": True, "dbsys": False},
            {"name": "IRISLIB", "default": False, "dbsys": True},
        ],
    }
    c = client_with(lambda r: httpx.Response(200, json=ok(content)))
    info = c.namespace_info("USER")
    assert info.default_db == "USER"
    assert info.is_system_db("IRISLIB") and not info.is_system_db("USER")


def test_export_joins_lines_with_lf() -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<Export generator="IRIS">', "àè €", "</Export>"]
    bodies: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=ok(lines))

    data = client_with(handler).export_xml("USER", "Demo.Unicode.cls")
    assert bodies == [["Demo.Unicode.cls"]]
    assert data == ("\n".join(lines) + "\n").encode("utf-8")


def test_export_missing_item_is_not_found() -> None:
    err = {"error": "ERROR #6308: Item 'X.cls' is invalid", "code": 6308}
    c = client_with(lambda r: httpx.Response(200, json=ok([], errors=[err])))
    with pytest.raises(NotFoundError, match="6308"):
        c.export_xml("USER", "X.cls")


def test_import_sends_lines_and_flags_and_parses_result() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["flags"] = request.url.params.get("flags")
        captured["body"] = json.loads(request.content)
        result = [{"file": "x.xml", "imported": ["DEMORTN.MAC", "A.cls"], "status": ""}]
        return httpx.Response(200, json=ok(result, console=["Load finished"]))

    res = client_with(handler).import_xml("USER", b"<Export>\n<a/>\n</Export>\n", file="x.xml", flags="cuk")
    assert captured["flags"] == "cuk"
    assert captured["body"] == [{"file": "x.xml", "content": ["<Export>", "<a/>", "</Export>"]}]
    assert res.imported == ("DEMORTN.mac", "A.cls") and res.ok and res.console == ("Load finished",)


def test_import_compile_error_is_reported_not_raised() -> None:
    result = [{"file": "x.xml", "imported": ["A.cls"], "status": "ERROR #5475: Error compiling"}]
    res = client_with(lambda r: httpx.Response(200, json=ok(result))).import_xml("USER", b"<Export/>")
    assert res.imported == ("A.cls",) and not res.ok and "5475" in (res.error or "")


def test_import_permission_error_in_status_errors_is_forbidden() -> None:
    err = {
        "error": "ERROR #5883: mapped from a database that you do not have write permission on",
        "code": 5883,
    }
    c = client_with(lambda r: httpx.Response(200, json=ok([], errors=[err])))
    with pytest.raises(ForbiddenError):
        c.import_xml("USER", b"<Export/>")


def test_doc_timestamp_from_etag() -> None:
    c = client_with(lambda r: httpx.Response(200, headers={"etag": "2026-09-25 08:31:58.268"}))
    assert c.doc_timestamp("USER", "Demo.Person.cls") == "2026-09-25 08:31:58.268"


# ---- error mapping ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, AuthError), (403, ForbiddenError), (404, NotFoundError), (500, ServerError), (503, ServerError)],
)
def test_http_errors(status: int, exc: type[Exception]) -> None:
    c = client_with(lambda r: httpx.Response(status))
    with pytest.raises(exc):
        c.list_docs("USER")


def test_status_errors_on_http_500_carry_message() -> None:
    body = ok([], errors=[{"error": "ERROR #5001: boom", "code": 5001}])
    c = client_with(lambda r: httpx.Response(500, json=body))
    with pytest.raises(ServerError, match="boom"):
        c.list_docs("USER")


def test_invalid_json_is_server_error() -> None:
    c = client_with(lambda r: httpx.Response(200, text="not json"))
    with pytest.raises(ServerError):
        c.list_docs("USER")


# ---- retries ---------------------------------------------------------------------------------
def test_connect_error_retried_once_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=ok([]))

    assert client_with(handler).list_docs("USER") == []
    assert attempts["n"] == 2


def test_connect_error_twice_raises_connection_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ConnectionFailed):
        client_with(handler).list_docs("USER")


def test_load_is_not_retried_after_timeout() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ConnectionFailed):
        client_with(handler).import_xml("USER", b"<Export/>")
    assert attempts["n"] == 1


def test_idempotent_timeout_is_retried() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=ok(["<Export/>"]))

    client_with(handler).export_xml("USER", "A.cls")
    assert attempts["n"] == 2


# ---- secrets never logged ---------------------------------------------------------------------
def test_password_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    c = client_with(lambda r: httpx.Response(401))
    with pytest.raises(AuthError):
        c.list_docs("USER")
    text = caplog.text
    assert "Pa55word!" not in text
    assert "authorization" not in text.lower()
    assert "UGE1NXdvcmQh" not in text  # base64 fragment of the password
