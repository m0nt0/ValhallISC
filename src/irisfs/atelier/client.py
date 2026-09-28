"""Synchronous, thread-safe client for the IRIS Atelier REST API (the API behind VS Code's isfs).

Endpoints were verified against IRIS 2026.1 (API v8); see doc/DECISIONS.md ADR-002.
One `httpx.Client` is shared by all threads so the CSP session cookie is reused (license friendly).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

from irisfs.atelier.errors import (
    AuthError,
    ConnectionFailed,
    ForbiddenError,
    NotExportableError,
    NotFoundError,
    ServerError,
    UnsupportedServer,
)
from irisfs.atelier.models import (
    DatabaseInfo,
    DocInfo,
    FolderEntry,
    ImportResult,
    NamespaceInfo,
    ServerInfo,
)

log = logging.getLogger(__name__)

MIN_API = 7  # action/xml/export and action/xml/load appeared in v7
_ROUTINE_EXTS = {"MAC", "INT", "INC", "BAS", "MVB", "MVI"}
_NOT_FOUND_CODES = {6308}  # ExportNoDef
_FORBIDDEN_CODES = {5883}  # item mapped from a database without write permission
_NOT_EXPORTABLE_CODES = {6309, 5848}  # deployed class (no source); default Studio project

# Parameters: Spec, Dir, OrderBy, SystemFiles, Flat, NotStudio, ShowGenerated, Filter, RoundTime, Mapped.
# "Date" must be quoted: DATE is an SQL reserved word.
_FOLDER_QUERY = (
    'SELECT {top}Name, Type, "Date" FROM %Library.RoutineMgr_StudioOpenDialog(?,?,?,?,?,?,?,?,?,?)'
)
_TYPE_PACKAGE = 9
_TYPE_CSP_DIR = 10
_TYPE_OTHER = 100  # lookup tables, DTL, BPL, HL7 schemas, ...: listed by full name at the root
_SLOW_MS = 1000  # requests slower than this are logged even without debug logging


def _folder_query(limit: int | None) -> str:
    return _FOLDER_QUERY.format(top=f"TOP {int(limit)} " if limit else "")


def _flag(value: bool) -> str:
    return "1" if value else "0"


def normalize_doc_name(name: str) -> str:
    """IRIS reports imported routines as 'NAME.MAC'; docnames uses 'NAME.mac'."""
    base, dot, ext = name.rpartition(".")
    if dot and ext.upper() in _ROUTINE_EXTS:
        return f"{base}.{ext.lower()}"
    return name


def xml_lines(data: bytes) -> list[str]:
    """Split an XML file into the line array the Atelier API expects."""
    text = data.decode("utf-8-sig")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


class AtelierClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout: float = 30.0,
        max_concurrency: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/atelier",
            auth=httpx.BasicAuth(username, password),
            verify=verify_tls,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            transport=transport,
            headers={"Accept": "application/json"},
        )
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._info_lock = threading.Lock()
        self._info: ServerInfo | None = None

    # ---- lifecycle ---------------------------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AtelierClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- transport ---------------------------------------------------------------------------
    def _request(self, method: str, path: str, *, idempotent: bool, **kwargs: Any) -> httpx.Response:
        attempts = 2
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                with self._slots:
                    response = self._http.request(method, path, **kwargs)
            except httpx.ConnectError as e:
                # Nothing reached the server: always safe to retry once.
                if attempt < attempts:
                    log.info("%s %s: connect failed (%s), retrying", method, path, e)
                    continue
                raise ConnectionFailed(f"Cannot connect to {self.base_url}: {e}") from e
            except httpx.TimeoutException as e:
                if idempotent and attempt < attempts:
                    log.info("%s %s: timeout, retrying", method, path)
                    continue
                raise ConnectionFailed(f"Timed out talking to {self.base_url}") from e
            except httpx.TransportError as e:
                if idempotent and attempt < attempts:
                    continue
                raise ConnectionFailed(f"Network error talking to {self.base_url}: {e}") from e
            # Never log headers or bodies: they may carry credentials or source code.
            ms = (time.perf_counter() - started) * 1000
            log.log(
                logging.INFO if ms >= _SLOW_MS else logging.DEBUG,
                "%s %s -> %s (%.0f ms, %s bytes)",
                method,
                path,
                response.status_code,
                ms,
                response.headers.get("content-length", "?"),
            )
            self._raise_for_http(response)
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_for_http(r: httpx.Response) -> None:
        if r.status_code < 400:
            return
        detail = ""
        try:
            errors = r.json().get("status", {}).get("errors", [])
            detail = "; ".join(str(e.get("error", e)) for e in errors)
        except ValueError:
            pass
        if r.status_code == 401:
            raise AuthError("Authentication failed: check user name and password", status_code=401)
        if r.status_code == 403:
            raise ForbiddenError(detail or "Access denied", status_code=403)
        if r.status_code == 404:
            raise NotFoundError(detail or f"Not found: {r.request.url.path}", status_code=404)
        if r.status_code == 503:
            raise ServerError(detail or "Service unavailable (license limit reached?)", status_code=503)
        raise ServerError(detail or f"HTTP {r.status_code}", status_code=r.status_code)

    @staticmethod
    def _body(r: httpx.Response) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError as e:
            raise ServerError(f"Invalid response from server (HTTP {r.status_code})") from e
        if not isinstance(body, dict):
            raise ServerError("Invalid response from server")
        return body

    @staticmethod
    def _raise_for_status(body: dict[str, Any]) -> None:
        errors = body.get("status", {}).get("errors", [])
        if not errors:
            return
        codes = {e.get("code") for e in errors if isinstance(e, dict)}
        message = "; ".join(str(e.get("error", e)) if isinstance(e, dict) else str(e) for e in errors)
        if codes & _NOT_FOUND_CODES:
            raise NotFoundError(message)
        if codes & _NOT_EXPORTABLE_CODES:
            raise NotExportableError(message)
        if codes & _FORBIDDEN_CODES:
            raise ForbiddenError(message)
        raise ServerError(message)

    def _versioned(self, ns: str, rest: str) -> str:
        return f"/v{self.server_info().api}/{quote(ns, safe='')}/{rest}"

    # ---- API ---------------------------------------------------------------------------------
    def server_info(self, *, refresh: bool = False) -> ServerInfo:
        """GET /api/atelier/ - also serves as the credential check."""
        with self._info_lock:
            if self._info is not None and not refresh:
                return self._info
            body = self._body(self._request("GET", "/", idempotent=True))
            content = body.get("result", {}).get("content", {})
            try:
                info = ServerInfo(
                    version=str(content["version"]),
                    api=int(content["api"]),
                    namespaces=tuple(str(n) for n in content["namespaces"]),
                )
            except (KeyError, TypeError, ValueError) as e:
                raise ServerError(
                    "Unexpected server info response (is this an IRIS Atelier endpoint?)"
                ) from e
            if info.api < MIN_API:
                raise UnsupportedServer(
                    f"Server Atelier API v{info.api} is too old; "
                    f"v{MIN_API}+ (IRIS 2023.2 or later) is required"
                )
            self._info = info
            return info

    def namespaces(self) -> list[str]:
        return list(self.server_info(refresh=True).namespaces)

    def namespace_info(self, ns: str) -> NamespaceInfo:
        body = self._body(
            self._request("GET", f"/v{self.server_info().api}/{quote(ns, safe='')}", idempotent=True)
        )
        self._raise_for_status(body)
        content = body.get("result", {}).get("content", {})
        dbs = tuple(
            DatabaseInfo(name=str(d["name"]), default=bool(d.get("default")), dbsys=bool(d.get("dbsys")))
            for d in content.get("db", [])
        )
        return NamespaceInfo(name=str(content.get("name", ns)), databases=dbs)

    def list_docs(
        self, ns: str, *, category: str = "*", generated: bool = False, like: str | None = None
    ) -> list[DocInfo]:
        """Every document of a namespace (`docnames`). `like` narrows it with an SQL LIKE pattern
        on the name ("Demo.Sub.%")."""
        params: dict[str, Any] = {"generated": 1 if generated else 0}
        if like is not None:
            params["filter"] = like
        r = self._request("GET", self._versioned(ns, f"docnames/{category}"), params=params, idempotent=True)
        body = self._body(r)
        self._raise_for_status(body)
        return [
            DocInfo(
                name=str(d["name"]),
                cat=str(d.get("cat", "")),
                ts=str(d.get("ts", "")),
                db=str(d.get("db", "")),
                upd=bool(d.get("upd", True)),
                gen=bool(d.get("gen", False)),
            )
            for d in body.get("result", {}).get("content", [])
        ]

    def list_folder(
        self,
        ns: str,
        package: str,
        *,
        system: bool,
        generated: bool,
        mapped: bool,
        limit: int | None = None,
    ) -> list[FolderEntry]:
        """One level of a namespace: the sub-packages and documents directly inside `package` ("" for the
        namespace root), through %Library.RoutineMgr_StudioOpenDialog - the query VS Code's isfs uses.

        `system` includes %-items, `generated` generated items, `mapped` items mapped from other databases.
        "Other" documents (lookup tables, DTL, BPL, ...) are only listed at the root, by full name.
        `limit` returns only the first rows (packages first): a folder can hold tens of thousands. It counts
        IRIS's rows, CSP folders (skipped here) included, so at the root it can return fewer entries."""
        spec = f"{package}/*" if package else "*"  # "Demo.Sub/*" (not "Demo/Sub/*": that lists nothing)
        params = [spec, "1", "1", _flag(system), "0", "0", _flag(generated), "", "0", _flag(mapped)]
        r = self._request(
            "POST",
            self._versioned(ns, "action/query"),
            json={"query": _folder_query(limit), "parameters": params},
            idempotent=True,
        )
        body = self._body(r)
        self._raise_for_status(body)
        entries: list[FolderEntry] = []
        for row in body.get("result", {}).get("content", []):
            name, kind = str(row.get("Name", "")), row.get("Type")
            if not name or kind == _TYPE_CSP_DIR:
                continue
            if kind == _TYPE_PACKAGE:
                entries.append(FolderEntry(name, is_dir=True))
            else:
                full = name if kind == _TYPE_OTHER or not package else f"{package}.{name}"
                entries.append(FolderEntry(full, is_dir=False, ts=str(row.get("Date", ""))))
        log.debug(
            "list_folder %s %r system=%s mapped=%s -> %d entries", ns, spec, system, mapped, len(entries)
        )
        return entries

    def doc_info(self, ns: str, name: str) -> DocInfo:
        """One document's metadata (database, timestamp) from `GET doc`: cost independent of the
        namespace's size, unlike `docnames`."""
        body = self._body(
            self._request("GET", self._versioned(ns, f"doc/{quote(name, safe='')}"), idempotent=True)
        )
        self._raise_for_status(body)
        r = body.get("result", {})
        return DocInfo(
            name=str(r.get("name", name)),
            cat=str(r.get("cat", "")),
            ts=str(r.get("ts", "")),
            db=str(r.get("db", "")),
            upd=bool(r.get("upd", True)),
            gen=False,
        )

    def doc_timestamp(self, ns: str, name: str) -> str:
        """Cheap freshness check: HEAD returns the document timestamp as ETag."""
        r = self._request("HEAD", self._versioned(ns, f"doc/{quote(name, safe='')}"), idempotent=True)
        return str(r.headers.get("etag", "")).strip('"')

    def export_xml(self, ns: str, name: str) -> bytes:
        """Legacy XML export of one document, identical to $SYSTEM.OBJ.Export (UTF-8, LF line ends)."""
        r = self._request("POST", self._versioned(ns, "action/xml/export"), json=[name], idempotent=True)
        body = self._body(r)
        self._raise_for_status(body)
        lines = body.get("result", {}).get("content", [])
        if not lines:
            raise NotFoundError(f"{name} has nothing to export")
        return ("\n".join(str(line) for line in lines) + "\n").encode("utf-8")

    def import_xml(
        self, ns: str, data: bytes, *, file: str = "irisfs.xml", flags: str = "ck"
    ) -> ImportResult:
        """Load (and compile, depending on `flags`) every document in an XML export file.

        Compile errors do not raise: the documents are saved and the error text is in `.error`.
        """
        payload = [{"file": file, "content": xml_lines(data)}]
        r = self._request(
            "POST",
            self._versioned(ns, "action/xml/load"),
            params={"flags": flags},
            json=payload,
            idempotent=False,  # never replay a load after a timeout
        )
        body = self._body(r)
        self._raise_for_status(body)
        results = body.get("result", {}).get("content", [])
        if not results:
            raise ServerError("Server returned no load result")
        first = results[0]
        status = str(first.get("status", "") or "").strip()
        return ImportResult(
            imported=tuple(normalize_doc_name(str(n)) for n in first.get("imported", [])),
            error=status or None,
            console=tuple(str(c) for c in body.get("console", [])),
        )

    def delete_doc(self, ns: str, name: str) -> None:
        body = self._body(
            self._request("DELETE", self._versioned(ns, f"doc/{quote(name, safe='')}"), idempotent=True)
        )
        self._raise_for_status(body)
