import json
import os
import stat
from pathlib import Path

import pytest

from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.config.secrets import FileSecretStore
from irisfs.config.store import (
    SCHEMA_VERSION,
    ProfileActiveError,
    ProfileNotFoundError,
    ProfileStore,
    ProfileStoreError,
)


def make(name: str = "Local", **kw: object) -> Profile:
    base: dict[str, object] = dict(name=name, host="localhost", username="_SYSTEM", mount_point="/tmp/iris")
    base.update(kw)
    return Profile(**base)  # type: ignore[arg-type]


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "cfg" / "profiles.json", tmp_path / "cfg" / "secrets.json"


def new_store(paths: tuple[Path, Path], **kw: object) -> ProfileStore:
    return ProfileStore(paths[0], secrets=FileSecretStore(paths[1]), system="Linux", **kw)  # type: ignore[arg-type]


def test_empty_when_missing(paths: tuple[Path, Path]) -> None:
    assert new_store(paths).profiles() == []


def test_crud_round_trip(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    p = store.add(make(), password="s3cret")
    assert store.password(p.id) == "s3cret"

    reloaded = new_store(paths)
    assert reloaded.profiles() == [p]

    p.port = 1972
    reloaded.update(p)
    assert new_store(paths).get(p.id).port == 1972
    assert reloaded.password(p.id) == "s3cret"  # password kept when not given

    reloaded.update(p, password="new")
    assert reloaded.password(p.id) == "new"

    reloaded.delete(p.id)
    assert new_store(paths).profiles() == []
    assert reloaded.password(p.id) is None  # secret deleted with the profile


def test_returned_profiles_are_copies(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    p = store.add(make())
    p.name = "changed"
    store.profiles()[0].name = "changed too"
    assert store.get(p.id).name == "Local"


def test_duplicate_names_rejected_case_insensitive(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    store.add(make("Local"))
    with pytest.raises(ProfileValidationError) as exc:
        store.add(make("LOCAL"))
    assert "name" in exc.value.errors


def test_rename_to_existing_name_rejected(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    store.add(make("A"))
    b = store.add(make("B"))
    b.name = "a"
    with pytest.raises(ProfileValidationError):
        store.update(b)


def test_update_same_name_same_profile_ok(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    a = store.add(make("A"))
    a.read_only = True
    assert store.update(a).read_only


def test_invalid_profile_not_saved(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    with pytest.raises(ProfileValidationError):
        store.add(make(host=""))
    assert not paths[0].exists()


def test_active_profile_cannot_be_updated_or_deleted(paths: tuple[Path, Path]) -> None:
    active: set[str] = set()
    store = new_store(paths, is_active=lambda pid: pid in active)
    p = store.add(make())
    active.add(p.id)
    with pytest.raises(ProfileActiveError):
        store.delete(p.id)
    with pytest.raises(ProfileActiveError):
        store.update(p)
    active.clear()
    store.delete(p.id)


def test_unknown_ids(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    with pytest.raises(ProfileNotFoundError):
        store.get("nope")
    with pytest.raises(ProfileNotFoundError):
        store.delete("nope")
    with pytest.raises(ProfileNotFoundError):
        store.update(make())


def test_password_never_written_to_profiles_file(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    store.add(make(), password="TopSecretValue")
    assert "TopSecretValue" not in paths[0].read_text()


def test_files_are_private(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    store.add(make(), password="x")
    if os.name == "posix":
        assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
        assert stat.S_IMODE(paths[1].stat().st_mode) == 0o600


def test_crash_during_write_keeps_old_file(paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    store = new_store(paths)
    store.add(make("A"))
    before = paths[0].read_text()

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("irisfs.config.store.os.replace", boom)
    with pytest.raises(OSError):
        store.add(make("B"))
    assert paths[0].read_text() == before
    assert not paths[0].with_name("profiles.json.tmp").exists()


def test_corrupt_file_is_backed_up(paths: tuple[Path, Path]) -> None:
    paths[0].parent.mkdir(parents=True)
    paths[0].write_text("{not json")
    store = new_store(paths)
    assert store.profiles() == []
    assert store.load_warning and "corrupt" in store.load_warning
    backups = list(paths[0].parent.glob("profiles.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"


def test_entry_missing_required_field_is_treated_as_corrupt(paths: tuple[Path, Path]) -> None:
    paths[0].parent.mkdir(parents=True)
    paths[0].write_text(json.dumps({"version": 1, "profiles": [{"name": "x"}]}))
    store = new_store(paths)
    assert store.profiles() == [] and store.load_warning


def test_newer_schema_is_refused_and_untouched(paths: tuple[Path, Path]) -> None:
    paths[0].parent.mkdir(parents=True)
    content = json.dumps({"version": SCHEMA_VERSION + 1, "profiles": []})
    paths[0].write_text(content)
    with pytest.raises(ProfileStoreError):
        new_store(paths)
    assert paths[0].read_text() == content


def test_file_without_version_is_accepted(paths: tuple[Path, Path]) -> None:
    paths[0].parent.mkdir(parents=True)
    paths[0].write_text(json.dumps({"profiles": [make().to_dict()]}))
    assert len(new_store(paths).profiles()) == 1


def test_find_by_name(paths: tuple[Path, Path]) -> None:
    store = new_store(paths)
    p = store.add(make("Prod IRIS"))
    found = store.find_by_name("prod iris")
    assert found is not None and found.id == p.id
    assert store.find_by_name("nope") is None
