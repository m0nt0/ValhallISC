import contextlib
import threading
import time

from irisfs.atelier.models import DocInfo
from irisfs.vfs.content import ContentCache
from irisfs.vfs.tree import DirNode, FileNode, TreeCache, build_tree, lookup


def d(name: str, ts: str = "t1") -> DocInfo:
    return DocInfo(name=name, cat="CLS", ts=ts, db="USER", upd=True, gen=False)


def test_build_and_lookup() -> None:
    root = build_tree(
        [d("Demo.Person.cls"), d("Demo.Sub.Thing.cls"), d("R.mac"), d("/csp/x.csp")], case_insensitive=False
    )
    assert sorted(root.children) == ["Demo", "R.mac.xml"]
    node = lookup(root, ("Demo", "Sub", "Thing.cls.xml"), case_insensitive=False)
    assert isinstance(node, FileNode) and node.doc.name == "Demo.Sub.Thing.cls"
    assert isinstance(lookup(root, ("Demo",), case_insensitive=False), DirNode)
    assert lookup(root, ("demo",), case_insensitive=False) is None
    assert lookup(root, ("Demo", "Person.cls.xml", "x"), case_insensitive=False) is None


def test_case_insensitive_lookup_and_collisions() -> None:
    root = build_tree([d("Demo.A.cls"), d("DEMO.B.cls"), d("X.mac"), d("x.mac")], case_insensitive=True)
    assert list(root.children) == ["DEMO", "X.mac.xml"]  # sorted input: "DEMO.B.cls" first; x.mac dropped
    assert isinstance(lookup(root, ("demo", "a.cls.xml"), case_insensitive=True), FileNode)


def test_tree_cache_ttl_and_invalidate() -> None:
    now = [0.0]
    calls: list[str] = []

    def loader(ns: str) -> list[DocInfo]:
        calls.append(ns)
        return [d("A.cls")]

    cache = TreeCache(loader, ttl=10, clock=lambda: now[0])
    cache.get("USER")
    cache.get("USER")
    assert calls == ["USER"]
    now[0] = 11
    cache.get("USER")
    assert calls == ["USER", "USER"]
    cache.invalidate("USER")
    cache.get("USER")
    assert len(calls) == 3


def test_content_cache_single_flight() -> None:
    cache = ContentCache()
    calls = []
    gate = threading.Event()

    def loader() -> bytes:
        calls.append(1)
        gate.wait(2)
        return b"x" * 10

    threads = [threading.Thread(target=cache.get, args=(("U", "A.cls", "t"), loader)) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1


def test_content_cache_eviction_keeps_sizes() -> None:
    cache = ContentCache(max_bytes=25)
    for i in range(3):
        cache.get(("U", f"{i}.cls", "t"), lambda: b"x" * 10)
    assert cache.total_bytes == 20  # the oldest entry was evicted
    loads = []
    assert cache.size(("U", "0.cls", "t"), lambda: loads.append(1) or b"") == 10  # size survives eviction
    assert loads == []


def test_content_cache_invalidate() -> None:
    cache = ContentCache()
    cache.get(("U", "A.cls", "t"), lambda: b"old")
    cache.invalidate("U", "A.cls")
    assert cache.get(("U", "A.cls", "t"), lambda: b"new") == b"new"


def test_oversized_item_not_cached() -> None:
    cache = ContentCache(max_bytes=5)
    assert cache.get(("U", "A.cls", "t"), lambda: b"x" * 10) == b"x" * 10
    assert cache.total_bytes == 0


def test_failed_load_is_not_left_in_flight() -> None:
    cache = ContentCache()

    def boom() -> bytes:
        raise RuntimeError("export failed")

    with contextlib.suppress(RuntimeError):
        cache.get(("U", "A.cls", "t"), boom)
    assert not cache.has_size(("U", "A.cls", "t"))
    assert cache.get(("U", "A.cls", "t"), lambda: b"ok") == b"ok"
