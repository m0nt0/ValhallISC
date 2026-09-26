"""In-memory stand-in for AtelierClient, used by unit tests.

Behaviour mirrors what IRIS 2026.1 does (see doc/DECISIONS.md ADR-002); the contract tests in
tests/integration/test_atelier_contract.py run the same assertions against this fake and the real server.
Conventions of the fake:
  * a class whose source contains "+++" fails to compile (import succeeds, error reported);
  * `readonly_namespaces` makes imports fail like IRIS error #5883;
  * `fail_next` holds exceptions raised by the next calls (FIFO), `offline=True` fails every call.
"""

from __future__ import annotations

import datetime as dt
import threading
import xml.etree.ElementTree as StdET
from collections import Counter
from dataclasses import dataclass, field

import defusedxml.ElementTree as ET
from defusedxml import DefusedXmlException

from irisfs.atelier.errors import ConnectionFailed, NotFoundError
from irisfs.atelier.models import DatabaseInfo, DocInfo, ImportResult, NamespaceInfo, ServerInfo

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'


@dataclass
class FakeDoc:
    info: DocInfo
    element: str  # serialized item element (<Class ...>...</Class>)


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def doc_name_of(el: StdET.Element) -> str | None:
    name = el.get("name")
    if not name:
        return None
    if el.tag == "Class":
        return f"{name}.cls"
    if el.tag == "Routine":
        return f"{name}.{(el.get('type') or 'MAC').lower()}"
    if el.tag == "Document":
        return name
    return None


def category_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "cls":
        return "CLS"
    if ext in ("mac", "int", "inc", "bas", "mvb", "mvi"):
        return "RTN"
    return "OTH"


@dataclass
class FakeAtelier:
    namespaces_: dict[str, dict[str, FakeDoc]] = field(default_factory=dict)
    api: int = 8
    readonly_namespaces: set[str] = field(default_factory=set)
    fail_next: list[Exception] = field(default_factory=list)
    offline: bool = False
    calls: Counter[str] = field(default_factory=Counter)
    closed: bool = False

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # ---- helpers for tests -------------------------------------------------------------------
    def add_namespace(self, ns: str) -> None:
        self.namespaces_.setdefault(ns, {})

    def add_doc(self, ns: str, name: str, body: str = "", *, db: str | None = None, upd: bool = True) -> None:
        """Register a document directly; `body` goes inside the item element."""
        self.add_namespace(ns)
        base, _, ext = name.rpartition(".")
        if ext == "cls":
            element = f'<Class name="{base}">{body}</Class>'
        elif ext in ("mac", "int", "inc"):
            element = f'<Routine name="{base}" type="{ext.upper()}"><![CDATA[{body}]]></Routine>'
        else:
            element = f'<Document name="{name}">{body}</Document>'
        info = DocInfo(name=name, cat=category_of(name), ts=_now(), db=db or ns, upd=upd, gen=False)
        self.namespaces_[ns][name] = FakeDoc(info, element)

    def _enter(self, op: str) -> None:
        self.calls[op] += 1
        if self.offline:
            raise ConnectionFailed("fake server offline")
        if self.fail_next:
            raise self.fail_next.pop(0)

    def _ns(self, ns: str) -> dict[str, FakeDoc]:
        if ns not in self.namespaces_:
            raise NotFoundError(f"Namespace {ns} not found", status_code=404)
        return self.namespaces_[ns]

    # ---- AtelierApi --------------------------------------------------------------------------
    def server_info(self, *, refresh: bool = False) -> ServerInfo:
        self._enter("server_info")
        return ServerInfo(version="IRIS fake", api=self.api, namespaces=tuple(sorted(self.namespaces_)))

    def namespaces(self) -> list[str]:
        return list(self.server_info(refresh=True).namespaces)

    def namespace_info(self, ns: str) -> NamespaceInfo:
        self._enter("namespace_info")
        self._ns(ns)
        return NamespaceInfo(
            name=ns,
            databases=(DatabaseInfo(ns, True, ns == "%SYS"), DatabaseInfo("IRISLIB", False, True)),
        )

    def list_docs(self, ns: str, *, category: str = "*", generated: bool = False) -> list[DocInfo]:
        self._enter("list_docs")
        with self._lock:
            docs = [d.info for d in self._ns(ns).values()]
        return [d for d in docs if category in ("*", d.cat)]

    def doc_timestamp(self, ns: str, name: str) -> str:
        self._enter("doc_timestamp")
        try:
            return self._ns(ns)[name].info.ts
        except KeyError:
            raise NotFoundError(f"{name} not found", status_code=404) from None

    def export_xml(self, ns: str, name: str) -> bytes:
        self._enter("export_xml")
        with self._lock:
            doc = self._ns(ns).get(name)
        if doc is None:
            raise NotFoundError(f"ERROR #6308: Item '{name}' is invalid or does not have any data to export")
        text = (
            f'{HEADER}<Export generator="IRIS" version="26" ts="{doc.info.ts}">\n{doc.element}\n</Export>\n'
        )
        return text.encode("utf-8")

    def import_xml(
        self, ns: str, data: bytes, *, file: str = "irisfs.xml", flags: str = "ck"
    ) -> ImportResult:
        self._enter("import_xml")
        docs = self._ns(ns)
        try:
            root = ET.fromstring(data)
        except (StdET.ParseError, DefusedXmlException) as e:
            return ImportResult((), f"ERROR #6301: SAX XML Parser Error: {e}")
        if root.tag != "Export" or not root.get("generator"):
            return ImportResult(
                (), "ERROR #6301: This does not appear to be a IRIS exported file, unable to import."
            )
        items = [(doc_name_of(el), el) for el in root]
        names = [(n, el) for n, el in items if n]
        if ns in self.readonly_namespaces:
            first = names[0][0] if names else "?"
            return ImportResult(
                (),
                f"ERROR #5883: Item '{first}' is mapped from a database "
                "that you do not have write permission on.",
            )
        imported: list[str] = []
        errors: list[str] = []
        with self._lock:
            for name, el in names:
                assert name is not None
                element = StdET.tostring(el, encoding="unicode").strip()
                info = DocInfo(name=name, cat=category_of(name), ts=_now(), db=ns, upd=True, gen=False)
                docs[name] = FakeDoc(info, element)
                imported.append(name)
                if "c" in flags.split("-")[0] and "+++" in element:
                    errors.append(f"ERROR #5475: Error compiling routine: {name}")
        return ImportResult(tuple(imported), "\n".join(errors) or None)

    def delete_doc(self, ns: str, name: str) -> None:
        self._enter("delete_doc")
        with self._lock:
            if self._ns(ns).pop(name, None) is None:
                raise NotFoundError(f"{name} not found", status_code=404)

    def close(self) -> None:
        self.closed = True
