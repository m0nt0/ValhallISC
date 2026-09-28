"""Plain data returned by the Atelier client."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServerInfo:
    version: str
    api: int
    namespaces: tuple[str, ...]


@dataclass(frozen=True)
class DatabaseInfo:
    name: str
    default: bool
    dbsys: bool


@dataclass(frozen=True)
class NamespaceInfo:
    name: str
    databases: tuple[DatabaseInfo, ...]

    @property
    def default_db(self) -> str | None:
        return next((d.name for d in self.databases if d.default), None)

    def is_system_db(self, name: str) -> bool:
        return any(d.name == name and d.dbsys for d in self.databases)


@dataclass(frozen=True)
class DocInfo:
    """One code document as listed by `docnames`. `ts` is IRIS local time 'YYYY-MM-DD hh:mm:ss.fff'."""

    name: str
    cat: str
    ts: str
    db: str
    upd: bool
    gen: bool


@dataclass(frozen=True)
class FolderEntry:
    """One entry of a one-level package listing (`AtelierApi.list_folder`).

    `name` is the package segment for a folder ("Sub") and the full document name for a document
    ("Demo.Sub.Thing.cls"). `ts` is the document's timestamp ("" for folders)."""

    name: str
    is_dir: bool
    ts: str = ""


@dataclass(frozen=True)
class ImportResult:
    """Outcome of loading one XML file. `error` holds IRIS's status text (compile or load errors)."""

    imported: tuple[str, ...]
    error: str | None = None
    console: tuple[str, ...] = field(default=(), compare=False)

    @property
    def ok(self) -> bool:
        return self.error is None
