"""One-level folder loading (ADR-014): what is fetched when, and parity with the whole-namespace listing."""

from __future__ import annotations

import time
from typing import Any

import pytest

from irisfs.atelier.errors import ServerError
from irisfs.atelier.models import DocInfo
from irisfs.vfs.errors import FsError
from irisfs.vfs.folders import FolderCache
from irisfs.vfs.tree import DirNode, FileNode
from irisfs.vfs.vfs import Options, VirtualFS
from tests.fakes.fake_atelier import FakeAtelier


def make_fake() -> FakeAtelier:
    f = FakeAtelier()
    f.add_doc("APP", "App.Person.cls")
    f.add_doc("APP", "App.Model.Order.cls")
    f.add_doc("APP", "App.Model.Deep.Line.cls")
    f.add_doc("APP", "APPRTN.mac")
    f.add_doc("APP", "AppInc.inc")
    f.add_doc("APP", "AppTable.LUT", db="@OTHER")  # "other" document at the root
    f.add_doc("APP", "App.Model.Flow.bpl", db="@OTHER")  # "other" document inside a package
    f.add_doc("APP", "%Lib.X.cls", db="IRISLIB")  # system: % prefix
    f.add_doc("APP", "Ens.Util.Thing.cls", db="ENSLIB")  # system: reserved name
    f.add_doc("APP", "CSPX.Dash.Chart.cls", db="ENSLIB")  # system: mapped from a system database only
    f.add_doc("APP", "Shared.Util.cls", db="SHAREDCODE")  # mapped from a user database: shown
    f.add_doc("APP", "Shared.Sub.Deep.cls", db="SHAREDCODE")
    f.add_doc("APP", "EnsJob.mac")  # reserved name in the namespace's own database
    return f


def walk(fs: VirtualFS, path: str = "/APP") -> set[str]:
    out: set[str] = set()
    for name in fs.readdir(path):
        child = f"{path}/{name}"
        out.add(child)
        if fs.getattr(child).is_dir:
            out |= walk(fs, child)
    return out


def full_listing_fs(fake: FakeAtelier, **opts: Any) -> VirtualFS:
    fs = VirtualFS(fake, Options(prefetch_workers=0, **opts))
    fs._full_listing.add("APP")  # force the fallback: the whole namespace, as before ADR-014
    return fs


EXPECTED = {
    "/APP/App",
    "/APP/App/Person.cls.xml",
    "/APP/App/Model",
    "/APP/App/Model/Order.cls.xml",
    "/APP/App/Model/Flow.bpl.xml",
    "/APP/App/Model/Deep",
    "/APP/App/Model/Deep/Line.cls.xml",
    "/APP/APPRTN.mac.xml",
    "/APP/AppInc.inc.xml",
    "/APP/AppTable.LUT.xml",
    "/APP/Shared",
    "/APP/Shared/Util.cls.xml",
    "/APP/Shared/Sub",
    "/APP/Shared/Sub/Deep.cls.xml",
}


def test_same_tree_as_the_whole_namespace_listing() -> None:
    fake = make_fake()
    lazy = walk(VirtualFS(fake, Options(prefetch_workers=0)))
    assert lazy == EXPECTED
    assert walk(full_listing_fs(fake)) == EXPECTED


def test_show_system_matches_the_whole_namespace_listing() -> None:
    fake = make_fake()
    lazy = walk(VirtualFS(fake, Options(prefetch_workers=0, show_system=True)))
    assert lazy == walk(full_listing_fs(fake, show_system=True))
    assert {"/APP/%Lib/X.cls.xml", "/APP/Ens/Util/Thing.cls.xml", "/APP/CSPX", "/APP/EnsJob.mac.xml"} <= lazy


def test_only_the_opened_folder_is_fetched() -> None:
    fake = make_fake()
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    assert "App" in fs.readdir("/APP")
    first = fake.calls["list_folder"]
    # Only the packages mapped from somewhere are placed (Shared: user database; CSPX: system database),
    # by reading one document of each. Ens is hidden by its reserved name without asking.
    assert fake.calls["doc_info"] == 2
    assert fs.readdir("/APP/App") == ["Model", "Person.cls.xml"]
    assert fake.calls["list_folder"] == first + 2  # this level only (mapped and local listings)
    assert fake.calls["list_docs"] == 0  # never the whole namespace


def test_stat_of_a_file_loads_only_its_ancestors() -> None:
    fake = make_fake()
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    assert not fs.getattr("/APP/App/Model/Deep/Line.cls.xml").is_dir
    assert not fs.folders.was_listed("APP", ("Shared",))


def test_stat_of_namespaces_and_impossible_names_asks_nothing() -> None:
    fake = make_fake()
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    assert fs.readdir("/") == ["APP"]
    assert fs.getattr("/APP").is_dir  # Finder stats every namespace shown: no listing for that
    for junk in (".DS_Store", ".localized", "Icon\r", "desktop.ini", "._App", "App.old"):
        with pytest.raises(FsError):
            fs.getattr(f"/APP/{junk}")
    assert fake.calls["list_folder"] == 0 and fake.calls["doc_info"] == 0


def test_package_mapped_from_user_database_is_classified_once() -> None:
    fake = make_fake()
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    fs.readdir("/APP")
    assert fake.calls["doc_info"] == 2  # Shared and CSPX
    walk(fs, "/APP/Shared")
    fs.readdir("/APP")
    assert fake.calls["doc_info"] == 2  # Shared's sub-packages inherit its database; results are kept


def test_only_system_databases_mapped_needs_no_classification() -> None:
    fake = make_fake()
    del fake.namespaces_["APP"]["Shared.Util.cls"], fake.namespaces_["APP"]["Shared.Sub.Deep.cls"]
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    assert "CSPX" not in fs.readdir("/APP")
    assert fake.calls["doc_info"] == 0 and fake.calls["list_docs"] == 0


def test_server_refusing_the_query_falls_back_to_the_whole_namespace() -> None:
    fake = make_fake()
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    fs.readdir("/")  # namespaces are cached: the next call to the server is the one-level query
    fake.fail_next.append(ServerError("SQLCODE -99 privilege violation"))
    assert walk(fs) == EXPECTED
    assert "APP" in fs._full_listing
    before = fake.calls["list_folder"]
    fs.refresh("APP")  # a refresh tries the one-level query again
    fs.readdir("/APP")
    assert fake.calls["list_folder"] > before


def test_new_document_appears_after_ttl_and_import_invalidates() -> None:
    fake = make_fake()
    now = [0.0]
    opts = Options(prefetch_workers=0, tree_ttl=10, background_refresh=False)
    fs = VirtualFS(fake, opts, clock=lambda: now[0])
    assert "New.cls.xml" not in fs.readdir("/APP/App")
    fake.add_doc("APP", "App.New.cls")
    assert "New.cls.xml" not in fs.readdir("/APP/App")  # cached
    now[0] = 11
    assert "New.cls.xml" in fs.readdir("/APP/App")


# ---- FolderCache on its own ------------------------------------------------------------------
def doc(name: str, ts: str = "t1") -> DocInfo:
    return DocInfo(name, "CLS", ts, "", True, False)


class Loader:
    def __init__(self) -> None:
        self.data: dict[tuple[str, ...], dict[str, DocInfo | None]] = {
            (): {"Pkg": None, "Top.cls.xml": doc("Top.cls")},
            ("Pkg",): {"A.cls.xml": doc("Pkg.A.cls")},
        }
        self.calls: list[tuple[str, ...]] = []
        self.fail = False

    def __call__(self, ns: str, parts: tuple[str, ...]) -> dict[str, DocInfo | None]:
        self.calls.append(parts)
        if self.fail:
            raise ConnectionError("down")
        return dict(self.data.get(parts, {}))


@pytest.fixture
def clocks() -> dict[str, float]:
    return {"mono": 0.0, "wall": 1000.0}


def cache(loader: Loader, clocks: dict[str, float], **kw: Any) -> FolderCache:
    return FolderCache(loader, clock=lambda: clocks["mono"], wall_clock=lambda: clocks["wall"], **kw)


def test_resolve_loads_ancestors_and_optionally_the_folder(clocks: dict[str, float]) -> None:
    loader = Loader()
    fc = cache(loader, clocks)
    node = fc.resolve("NS", ("Pkg",), load_last=False)
    assert isinstance(node, DirNode) and loader.calls == [()]
    node = fc.resolve("NS", ("Pkg",))
    assert isinstance(node, DirNode) and "A.cls.xml" in node.children
    assert isinstance(fc.resolve("NS", ("Pkg", "A.cls.xml")), FileNode)
    assert fc.resolve("NS", ("Nope",)) is None
    assert fc.resolve("NS", ("Top.cls.xml", "x")) is None


def test_case_insensitive_resolve(clocks: dict[str, float]) -> None:
    fc = cache(Loader(), clocks, case_insensitive=True)
    assert isinstance(fc.resolve("NS", ("pkg", "a.CLS.xml")), FileNode)
    assert fc.resolve("NS", ("pkg",), load_last=False) is not None


def test_change_time_moves_only_on_real_changes(clocks: dict[str, float]) -> None:
    loader = Loader()
    fc = cache(loader, clocks, ttl=5)
    assert fc.changed_at("NS") is None
    fc.get("NS", ())
    fc.get("NS", ("Pkg",))  # first listing of another folder: not a change
    assert fc.changed_at("NS") == 1000.0
    clocks["mono"], clocks["wall"] = 10, 1010
    fc.get("NS", ("Pkg",))  # reloaded, same content
    assert fc.changed_at("NS") == 1000.0
    loader.data[("Pkg",)]["B.cls.xml"] = doc("Pkg.B.cls")
    fc.invalidate("NS")
    fc.get("NS", ("Pkg",))
    assert fc.changed_at("NS") == 1010.0


def test_failed_load_then_success_moves_change_time(clocks: dict[str, float]) -> None:
    loader = Loader()
    fc = cache(loader, clocks, ttl=0)
    fc.get("NS", ())
    loader.fail = True
    with pytest.raises(ConnectionError):
        fc.get("NS", ())
    loader.fail = False
    fc.get("NS", ())  # same content as before the outage, but clients may cache "not found" answers
    assert fc.changed_at("NS") == 1001.0  # strictly later even within the same second


def test_expired_listing_is_served_at_once_and_reloaded_in_background() -> None:
    fake = make_fake()
    now = [0.0]
    fs = VirtualFS(fake, Options(prefetch_workers=0, tree_ttl=10), clock=lambda: now[0])
    assert "New.cls.xml" not in fs.readdir("/APP/App")
    fake.add_doc("APP", "App.New.cls")
    now[0] = 11
    assert "New.cls.xml" not in fs.readdir("/APP/App")  # stale, returned without waiting for IRIS
    deadline = time.monotonic() + 5
    while "New.cls.xml" not in fs.readdir("/APP/App"):  # the background reload lands shortly
        assert time.monotonic() < deadline, "background reload never happened"
        time.sleep(0.01)
    fs.close()


def test_background_runner_reloads_once_and_keeps_serving(clocks: dict[str, float]) -> None:
    loader = Loader()
    fc = cache(loader, clocks, ttl=5, background=lambda task: task())
    fc.get("NS", ("Pkg",))
    loader.data[("Pkg",)]["B.cls.xml"] = doc("Pkg.B.cls")
    clocks["mono"] = 6
    assert "B.cls.xml" not in fc.get("NS", ("Pkg",)).children  # served stale, reload runs (here: at once)
    assert "B.cls.xml" in fc.get("NS", ("Pkg",)).children
    assert loader.calls == [("Pkg",), ("Pkg",)]


def test_invalidate_waits_for_fresh_content(clocks: dict[str, float]) -> None:
    loader = Loader()
    fc = cache(loader, clocks, ttl=5, background=lambda task: task())
    fc.get("NS", ("Pkg",))
    loader.data[("Pkg",)]["B.cls.xml"] = doc("Pkg.B.cls")
    fc.invalidate("NS")  # after an import: the next access must see IRIS's new state
    assert "B.cls.xml" in fc.get("NS", ("Pkg",)).children


def test_default_projects_and_deployed_classes_are_read_only() -> None:
    fake = make_fake()
    fake.add_doc("APP", "Default_me.prj", db="@OTHER")
    fake.deployed.add("Default_me.prj")
    fs = VirtualFS(fake, Options(prefetch_workers=0))
    assert fs.getattr("/APP/Default_me.prj.xml").mode == 0o444
    before = fake.calls["export_xml"]
    fs.getattr("/APP/Default_me.prj.xml")
    assert fake.calls["export_xml"] == before
