"""VirtualFS behaviour against the in-memory fake (plan Phase 4)."""

from __future__ import annotations

import errno
import os
import random
import threading
import time
from typing import Any

import pytest

from irisfs.atelier.errors import AuthError, ConnectionFailed
from irisfs.vfs.errors import FsError
from irisfs.vfs.vfs import GHOST_TTL, Options, VirtualFS
from tests.fakes.fake_atelier import FakeAtelier
from tests.integration.xmlsamples import class_xml, routine_xml

PERSON = "/USER/Demo/Person.cls.xml"


@pytest.fixture
def fake() -> FakeAtelier:
    f = FakeAtelier()
    f.add_doc("USER", "Demo.Person.cls", "<Description>person</Description>")
    f.add_doc("USER", "Demo.Sub.Thing.cls", "<Description>thing</Description>")
    f.add_doc("USER", "DEMORTN.mac", "DEMORTN ; hi\n quit")
    f.add_doc("USER", "DemoTable.LUT", "<entry/>", db="@OTHER")
    f.add_doc("USER", "%Lib.X.cls", "<Description>system</Description>", db="IRISLIB")
    f.add_doc("USER", "Ens.Gen.mac", "x", upd=False)
    f.add_doc("TESTNS", "Test.A.cls", "<Description>a</Description>")
    for i in range(20):
        f.add_doc("TESTNS", f"Many.C{i}.cls", f"<Description>{'x' * (i * 100)}</Description>")
    return f


class Env:
    def __init__(self, fake: FakeAtelier, **opts: Any) -> None:
        self.now = [1000.0]
        self.events: list[dict[str, Any]] = []
        self.fake = fake
        self.fs = VirtualFS(fake, Options(**opts), on_event=self.events.append, clock=lambda: self.now[0])

    def read_all(self, path: str) -> bytes:
        fh = self.fs.open(path, os.O_RDONLY)
        try:
            return self.fs.read(path, 10_000_000, 0, fh)
        finally:
            self.fs.release(path, fh)

    def copy_in(self, path: str, data: bytes, chunk: int = 7) -> None:
        """What `cp` does: create, write in chunks, flush, release."""
        fh = self.fs.create(path, 0o644)
        for off in range(0, len(data), chunk):
            self.fs.write(path, data[off : off + chunk], off, fh)
        try:
            self.fs.flush(path, fh)
        finally:
            self.fs.release(path, fh)


@pytest.fixture
def env(fake: FakeAtelier) -> Env:
    return Env(fake)


def errno_of(fn: Any, *args: Any) -> int:
    with pytest.raises(FsError) as exc:
        fn(*args)
    return exc.value.errno


# ---- listing and attributes ------------------------------------------------------------------
def test_root_lists_namespaces(env: Env) -> None:
    assert env.fs.readdir("/") == ["TESTNS", "USER"]
    assert env.fs.getattr("/").is_dir


def test_namespace_and_package_dirs(env: Env) -> None:
    assert env.fs.readdir("/USER") == ["DEMORTN.mac.xml", "Demo", "DemoTable.LUT.xml"]
    assert env.fs.readdir("/USER/Demo") == ["Person.cls.xml", "Sub"]
    assert env.fs.readdir("/USER/Demo/Sub") == ["Thing.cls.xml"]
    assert env.fs.getattr("/USER/Demo").is_dir


def test_system_items_shown_with_flag(fake: FakeAtelier) -> None:
    e = Env(fake, show_system=True)
    assert "%Lib" in e.fs.readdir("/USER")
    assert "Ens" in e.fs.readdir("/USER")


def test_unknown_paths(env: Env) -> None:
    assert errno_of(env.fs.getattr, "/NOPE") == errno.ENOENT
    assert errno_of(env.fs.getattr, "/USER/Demo/Nope.cls.xml") == errno.ENOENT
    assert errno_of(env.fs.readdir, PERSON) == errno.ENOTDIR


def test_file_size_and_mtime(env: Env, fake: FakeAtelier) -> None:
    attr = env.fs.getattr(PERSON)
    assert not attr.is_dir
    assert attr.size == len(fake.export_xml("USER", "Demo.Person.cls"))
    assert attr.mtime > 1_700_000_000
    assert attr.mode == 0o644


def test_read_only_mode_bits(fake: FakeAtelier) -> None:
    e = Env(fake, read_only=True)
    assert e.fs.getattr(PERSON).mode == 0o444
    assert e.fs.getattr("/USER").mode == 0o555


def test_case_insensitive_lookup(fake: FakeAtelier) -> None:
    e = Env(fake, case_insensitive=True)
    assert not e.fs.getattr("/user/demo/person.CLS.xml").is_dir
    assert errno_of(Env(fake).fs.getattr, "/user") == errno.ENOENT


# ---- reading ---------------------------------------------------------------------------------
def test_read_offsets(env: Env, fake: FakeAtelier) -> None:
    full = fake.export_xml("USER", "Demo.Person.cls")
    fh = env.fs.open(PERSON, os.O_RDONLY)
    assert env.fs.read(PERSON, 10, 0, fh) == full[:10]
    assert env.fs.read(PERSON, 10, 5, fh) == full[5:15]
    assert env.fs.read(PERSON, 100, len(full) - 3, fh) == full[-3:]
    assert env.fs.read(PERSON, 10, len(full), fh) == b""
    assert env.fs.read(PERSON, 10, len(full) + 50, fh) == b""
    env.fs.release(PERSON, fh)


def test_export_is_cached_per_timestamp(env: Env, fake: FakeAtelier) -> None:
    env.read_all(PERSON)
    env.fs.getattr(PERSON)
    env.read_all(PERSON)
    assert fake.calls["export_xml"] == 1


def test_open_directory_is_eisdir(env: Env) -> None:
    assert errno_of(env.fs.open, "/USER/Demo", os.O_RDONLY) == errno.EISDIR


# ---- writing / importing -----------------------------------------------------------------------
def test_copy_in_new_class_imports_and_compiles(env: Env, fake: FakeAtelier) -> None:
    env.copy_in("/USER/Demo/New.cls.xml", class_xml("Demo.New"))
    assert fake.calls["import_xml"] == 1
    assert env.events[-1]["event"] == "imported"
    assert env.events[-1]["items"] == ["Demo.New.cls"] and env.events[-1]["compile_errors"] == []
    assert "New.cls.xml" in env.fs.readdir("/USER/Demo")  # tree invalidated after import


def test_copy_in_uses_profile_flags(fake: FakeAtelier) -> None:
    calls: list[str] = []
    original = fake.import_xml

    def spy(ns: str, data: bytes, *, file: str = "x", flags: str = "ck") -> Any:
        calls.append(flags)
        return original(ns, data, file=file, flags=flags)

    fake.import_xml = spy  # type: ignore[method-assign]
    Env(fake, compile_flags="ck").copy_in("/USER/a.xml", class_xml("Demo.F1"))
    Env(fake, compile_on_import=False).copy_in("/USER/b.xml", class_xml("Demo.F2"))
    assert calls == ["ck", "-c"]


def test_overwrite_existing_with_trunc(env: Env, fake: FakeAtelier) -> None:
    fh = env.fs.open(PERSON, os.O_WRONLY | os.O_TRUNC)
    data = class_xml("Demo.Person", description="replaced")
    env.fs.write(PERSON, data, 0, fh)
    env.fs.release(PERSON, fh)
    assert b"replaced" in fake.export_xml("USER", "Demo.Person.cls")
    assert b"replaced" in env.read_all(PERSON)  # content cache invalidated


def test_partial_write_starts_from_current_export(env: Env, fake: FakeAtelier) -> None:
    before = fake.export_xml("USER", "Demo.Person.cls")
    fh = env.fs.open(PERSON, os.O_RDWR)
    assert env.fs.read(PERSON, 20, 0, fh) == before[:20]
    idx = before.index(b"person")
    env.fs.write(PERSON, b"PERSON", idx, fh)
    env.fs.release(PERSON, fh)
    assert b"<Description>PERSON</Description>" in fake.export_xml("USER", "Demo.Person.cls")


@pytest.mark.parametrize("flags", [os.O_RDWR, os.O_WRONLY])
def test_open_for_write_without_writing_never_imports(env: Env, fake: FakeAtelier, flags: int) -> None:
    fh = env.fs.open(PERSON, flags)
    env.fs.flush(PERSON, fh)
    env.fs.release(PERSON, fh)
    assert fake.calls["import_xml"] == 0 and env.events == []


def test_many_reads_never_import(env: Env, fake: FakeAtelier) -> None:
    for _ in range(200):
        env.read_all(PERSON)
        env.fs.getattr(PERSON)
    assert fake.calls["import_xml"] == 0


def test_two_handles_import_once_on_last_close(env: Env, fake: FakeAtelier) -> None:
    path = "/USER/two.xml"
    data = class_xml("Demo.Two")
    fh1 = env.fs.create(path, 0o644)
    fh2 = env.fs.open(path, os.O_WRONLY)
    env.fs.write(path, data, 0, fh1)
    env.fs.release(path, fh1)
    assert fake.calls["import_xml"] == 0
    env.fs.release(path, fh2)
    assert fake.calls["import_xml"] == 1


def test_new_file_visible_while_being_written(env: Env) -> None:
    path = "/USER/Demo/Wip.cls.xml"
    fh = env.fs.create(path, 0o644)
    env.fs.write(path, b"abc", 0, fh)
    assert env.fs.getattr(path).size == 3
    assert "Wip.cls.xml" in env.fs.readdir("/USER/Demo")
    env.fs.truncate(path, 1)
    assert env.fs.getattr(path).size == 1
    env.fs.release(path, fh)


def test_write_at_offset_beyond_end_zero_fills(env: Env) -> None:
    path = "/USER/gap.xml"
    fh = env.fs.create(path, 0o644)
    env.fs.write(path, b"B", 3, fh)
    assert env.fs.read(path, 10, 0, fh) == b"\0\0\0B"
    env.fs.release(path, fh)


# ---- rejections ------------------------------------------------------------------------------
def test_non_xml_rejected(env: Env, fake: FakeAtelier) -> None:
    assert errno_of(env.fs.create, "/USER/notes.txt", 0o644) == errno.EACCES
    assert fake.calls["import_xml"] == 0


def test_create_at_root_or_missing_dir(env: Env) -> None:
    assert errno_of(env.fs.create, "/x.xml", 0o644) == errno.EACCES
    assert errno_of(env.fs.create, "/USER/NoDir/x.xml", 0o644) == errno.ENOENT
    assert errno_of(env.fs.create, "/NOPE/x.xml", 0o644) == errno.ENOENT


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("unlink", (PERSON,)),
        ("rename", (PERSON, "/USER/Demo/Other.cls.xml")),
        ("mkdir", ("/USER/NewDir", 0o755)),
        ("rmdir", ("/USER/Demo",)),
    ],
)
def test_unsupported_ops_eperm(env: Env, fake: FakeAtelier, op: str, args: tuple[Any, ...]) -> None:
    assert errno_of(getattr(env.fs, op), *args) == errno.EPERM
    assert "Demo.Person.cls" in {d.name for d in fake.list_docs("USER")}


def test_truncate_closed_document_eperm(env: Env) -> None:
    assert errno_of(env.fs.truncate, PERSON, 0) == errno.EPERM


def test_read_only_refuses_all_mutations(fake: FakeAtelier) -> None:
    e = Env(fake, read_only=True)
    fs = e.fs
    assert errno_of(fs.create, "/USER/a.xml", 0o644) == errno.EROFS
    assert errno_of(fs.open, PERSON, os.O_WRONLY) == errno.EROFS
    assert errno_of(fs.open, PERSON, os.O_RDWR | os.O_TRUNC) == errno.EROFS
    assert errno_of(fs.truncate, PERSON, 0) == errno.EROFS
    assert errno_of(fs.unlink, PERSON) == errno.EROFS
    assert errno_of(fs.mkdir, "/USER/x", 0o755) == errno.EROFS
    assert errno_of(fs.chmod, PERSON, 0o600) == errno.EROFS
    assert e.read_all(PERSON)  # reads still work
    assert fake.calls["import_xml"] == 0


# ---- bad content -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "data",
    [
        b"<Export><Class name='x'>",
        b"<notexport/>",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><Export>&e;</Export>',
    ],
    ids=["malformed", "wrong-root", "xxe"],
)
def test_bad_xml_fails_flush_and_is_not_imported(env: Env, fake: FakeAtelier, data: bytes) -> None:
    with pytest.raises(FsError) as exc:
        env.copy_in("/USER/bad.xml", data)
    assert exc.value.errno == errno.EIO
    assert fake.calls["import_xml"] == 0
    assert env.events[-1]["event"] == "import_failed" and env.events[-1]["file"] == "bad.xml"


def test_compile_error_reported_in_event(env: Env, fake: FakeAtelier) -> None:
    env.copy_in("/USER/c.xml", class_xml("Demo.Broken", body="quit 1 +++ )"))
    ev = env.events[-1]
    assert ev["event"] == "imported" and ev["items"] == ["Demo.Broken.cls"] and ev["compile_errors"]


def test_permission_denied_reported(fake: FakeAtelier) -> None:
    fake.readonly_namespaces.add("USER")
    e = Env(fake)
    e.copy_in("/USER/p.xml", class_xml("Demo.P"))
    assert e.events[-1]["event"] == "import_failed" and "5883" in e.events[-1]["message"]


def test_server_down_during_import_reported(env: Env, fake: FakeAtelier) -> None:
    env.fs.readdir("/USER")  # warm namespace cache so only the import call fails
    fake.fail_next.append(ConnectionFailed("down"))
    env.copy_in("/USER/x.xml", class_xml("Demo.X"))
    assert env.events[-1]["event"] == "import_failed" and "down" in env.events[-1]["message"]


# ---- multi-item files, ghosts ----------------------------------------------------------------
def test_multi_item_file_and_ghost_entry(env: Env, fake: FakeAtelier) -> None:
    head = b'<?xml version="1.0" encoding="UTF-8"?>\n<Export generator="IRIS" version="26">\n'
    body = class_xml("Demo.M1").split(b"\n", 2)[2].rsplit(b"</Export>", 1)[0]
    rtn = routine_xml("MRTN").split(b"\n", 2)[2].rsplit(b"</Export>", 1)[0]
    env.copy_in("/USER/bundle.xml", head + body + rtn + b"</Export>\n")
    assert sorted(env.events[-1]["items"]) == ["Demo.M1.cls", "MRTN.mac"]
    assert "bundle.xml" in env.fs.readdir("/USER")  # ghost so cp's final stat succeeds
    assert env.fs.getattr("/USER/bundle.xml").size > 0
    assert "MRTN.mac.xml" in env.fs.readdir("/USER")
    env.now[0] += GHOST_TTL + 1
    assert "bundle.xml" not in env.fs.readdir("/USER")
    assert errno_of(env.fs.getattr, "/USER/bundle.xml") == errno.ENOENT


def test_matching_filename_leaves_no_ghost(env: Env) -> None:
    env.copy_in("/USER/Demo/Fresh.cls.xml", class_xml("Demo.Fresh"))
    assert env.fs.readdir("/USER/Demo").count("Fresh.cls.xml") == 1
    env.now[0] += GHOST_TTL + 1
    assert "Fresh.cls.xml" in env.fs.readdir("/USER/Demo")  # it's the real document now


def test_ghost_can_be_unlinked(env: Env) -> None:
    env.copy_in("/USER/g.xml", class_xml("Demo.G"))
    env.fs.unlink("/USER/g.xml")
    assert "g.xml" not in env.fs.readdir("/USER")


# ---- OS junk files -----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["._Person.cls.xml", ".DS_Store", "desktop.ini", "Thumbs.db"])
def test_junk_files_live_in_memory_only(env: Env, fake: FakeAtelier, name: str) -> None:
    path = f"/USER/Demo/{name}"
    fh = env.fs.create(path, 0o644)
    env.fs.write(path, b"\x00\x05junk", 0, fh)
    env.fs.release(path, fh)
    assert name in env.fs.readdir("/USER/Demo")
    assert env.read_all(path) == b"\x00\x05junk"
    env.fs.unlink(path)
    assert name not in env.fs.readdir("/USER/Demo")
    assert fake.calls["import_xml"] == 0


def test_junk_allowed_even_read_only(fake: FakeAtelier) -> None:
    e = Env(fake, read_only=True)
    e.fs.release("/USER/.DS_Store", e.fs.create("/USER/.DS_Store", 0o644))


# ---- metadata no-ops -------------------------------------------------------------------------
def test_metadata_ops_accepted(env: Env) -> None:
    path = "/USER/meta.xml"
    fh = env.fs.create(path, 0o644)
    env.fs.chmod(path, 0o600)
    env.fs.chown(path, 0, 0)
    env.fs.utimens(path, (0, 0))
    env.fs.setxattr(path, "com.apple.FinderInfo", b"x" * 32, 0)
    env.fs.release(path, fh)
    env.fs.chmod(PERSON, 0o600)  # real documents: accepted, nothing stored
    env.fs.utimens(PERSON)
    assert env.fs.listxattr(PERSON) == []
    assert errno_of(env.fs.getxattr, PERSON, "user.x") in (errno.ENODATA, getattr(errno, "ENOATTR", -1))


# ---- errors from IRIS --------------------------------------------------------------------------
def test_connection_errors_become_eio_and_recover(env: Env, fake: FakeAtelier) -> None:
    env.fs.readdir("/USER")
    fake.offline = True
    env.fs.refresh()
    assert errno_of(env.fs.readdir, "/") == errno.EIO
    fake.offline = False
    assert "USER" in env.fs.readdir("/")


def test_auth_error_is_eacces(env: Env, fake: FakeAtelier) -> None:
    fake.fail_next.append(AuthError("bad"))
    assert errno_of(env.fs.readdir, "/") == errno.EACCES


def test_bad_handle(env: Env) -> None:
    assert errno_of(env.fs.read, PERSON, 1, 0, 999) == errno.EBADF
    env.fs.release(PERSON, 999)  # releasing unknown handles is harmless


# ---- concurrency -----------------------------------------------------------------------------
def test_parallel_random_reads(env: Env, fake: FakeAtelier) -> None:
    paths = [f"/TESTNS/Many/C{i}.cls.xml" for i in range(20)]
    expected = {p: fake.export_xml("TESTNS", f"Many.C{i}.cls") for i, p in enumerate(paths)}
    fake.calls.clear()
    errors: list[BaseException] = []

    def worker(seed: int) -> None:
        rnd = random.Random(seed)
        try:
            for _ in range(50):
                p = rnd.choice(paths)
                fh = env.fs.open(p, os.O_RDONLY)
                off = rnd.randrange(0, len(expected[p]))
                assert env.fs.read(p, 64, off, fh) == expected[p][off : off + 64]
                env.fs.release(p, fh)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert fake.calls["export_xml"] == 20  # each document exported exactly once


def test_readdir_prefetches_sizes(env: Env, fake: FakeAtelier) -> None:
    env.fs.readdir("/TESTNS/Many")
    deadline = time.monotonic() + 5
    while fake.calls["export_xml"] < 20 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert fake.calls["export_xml"] == 20
    for i in range(20):
        env.fs.getattr(f"/TESTNS/Many/C{i}.cls.xml")
    assert fake.calls["export_xml"] == 20  # stats reuse the prefetched exports


def test_prefetch_can_be_disabled(fake: FakeAtelier) -> None:
    e = Env(fake, prefetch_workers=0)
    e.fs.readdir("/TESTNS/Many")
    assert fake.calls["export_xml"] == 0


def test_spotlight_marker(fake: FakeAtelier) -> None:
    e = Env(fake, no_index_marker=True)
    assert ".metadata_never_index" in e.fs.readdir("/")
    assert e.fs.getattr("/.metadata_never_index").size == 0
    assert ".metadata_never_index" not in Env(fake).fs.readdir("/")
