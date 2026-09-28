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
import re
import threading
import xml.etree.ElementTree as StdET
from collections import Counter
from dataclasses import dataclass, field

import defusedxml.ElementTree as ET
from defusedxml import DefusedXmlException

from irisfs.atelier.errors import ConnectionFailed, DeployedError, NotFoundError
from irisfs.atelier.models import (
    DatabaseInfo,
    DocInfo,
    FolderEntry,
    ImportResult,
    NamespaceInfo,
    ServerInfo,
)

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
SYSTEM_DBS = {"IRISLIB", "IRISSYS", "ENSLIB"}  # any other database named by add_doc(db=...) is a user one


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


def _like_regex(like: str) -> re.Pattern[str]:
    """SQL LIKE pattern ('%' any run, '_' one character) as a regular expression."""
    return re.compile("".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in like))


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
    deployed: set[str] = field(default_factory=set)  # document names in deployed mode (export fails #6309)
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
        with self._lock:
            used = {d.info.db for d in self._ns(ns).values()} - {ns, "IRISLIB"}
        extra = tuple(
            DatabaseInfo(db, False, db in SYSTEM_DBS) for db in sorted(used) if not db.startswith("@")
        )
        return NamespaceInfo(
            name=ns,
            databases=(DatabaseInfo(ns, True, ns == "%SYS"), DatabaseInfo("IRISLIB", False, True), *extra),
        )

    def list_docs(
        self, ns: str, *, category: str = "*", generated: bool = False, like: str | None = None
    ) -> list[DocInfo]:
        self._enter("list_docs")
        with self._lock:
            docs = [d.info for d in self._ns(ns).values()]
        pattern = _like_regex(like) if like is not None else None
        return [
            d for d in docs if category in ("*", d.cat) and (pattern is None or pattern.fullmatch(d.name))
        ]

    def list_folder(
        self, ns: str, package: str, *, system: bool, generated: bool, mapped: bool
    ) -> list[FolderEntry]:
        """Like IRIS's StudioOpenDialog: one level, "other" documents only at the root by full name,
        mapped = from a database other than the namespace's own."""
        self._enter("list_folder")
        with self._lock:
            docs = [d.info for d in self._ns(ns).values()]
        prefix = f"{package}." if package else ""
        dirs: set[str] = set()
        entries: list[FolderEntry] = []
        for d in docs:
            is_mapped = d.db != ns and not d.db.startswith("@")  # IRIS lists @OTHER documents as local
            hidden_system = (
                not system and d.name.startswith("%") and d.cat != "OTH"
            )  # IRIS lists %*.LUT anyway
            if hidden_system or (not mapped and is_mapped):
                continue
            if d.gen and not generated:
                continue
            if d.cat == "OTH":
                if not package:
                    entries.append(FolderEntry(d.name, is_dir=False, ts=d.ts))
                continue
            if not d.name.startswith(prefix):
                continue
            base = d.name[len(prefix) :].rpartition(".")[0]
            if "." in base:
                dirs.add(base.split(".", 1)[0])
            else:
                entries.append(FolderEntry(d.name, is_dir=False, ts=d.ts))
        return [FolderEntry(n, is_dir=True) for n in sorted(dirs)] + sorted(entries, key=lambda e: e.name)

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
        if name in self.deployed:
            raise DeployedError(f"ERROR #6309: Class '{name}' is in deployed mode and so can not be exported")
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
