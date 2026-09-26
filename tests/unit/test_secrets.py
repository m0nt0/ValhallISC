from pathlib import Path

import pytest

from irisfs.config import secrets
from irisfs.config.secrets import FileSecretStore


def test_file_store_round_trip(tmp_path: Path) -> None:
    s = FileSecretStore(tmp_path / "s.json")
    assert s.get("a") is None
    s.set("a", "pw1")
    s.set("b", "pw2")
    assert FileSecretStore(tmp_path / "s.json").get("a") == "pw1"
    s.delete("a")
    s.delete("missing")  # no error
    assert s.get("a") is None and s.get("b") == "pw2"


def test_file_store_survives_garbage(tmp_path: Path) -> None:
    (tmp_path / "s.json").write_text("garbage")
    s = FileSecretStore(tmp_path / "s.json")
    assert s.get("a") is None
    s.set("a", "x")
    assert s.get("a") == "x"


def test_default_store_falls_back_to_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "keyring_usable", lambda: False)
    store = secrets.default_store(tmp_path)
    assert isinstance(store, FileSecretStore)
    assert store.path == tmp_path / "secrets.json"


def test_fail_backend_is_not_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    assert secrets.keyring_usable() is False


class MemoryKeyring:
    """Minimal in-memory keyring backend (avoids touching the real OS keychain in tests)."""

    priority = 1

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, user: str) -> str | None:
        return self.data.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        self.data[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        from keyring.errors import PasswordDeleteError

        if (service, user) not in self.data:
            raise PasswordDeleteError(user)
        del self.data[(service, user)]


def test_keyring_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    backend = MemoryKeyring()
    for name in ("get_keyring", "get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, getattr(backend, name, lambda: backend))
    store = secrets.KeyringSecretStore(service="irisfs-test")
    assert "MemoryKeyring" in store.description
    store.set("p1", "pw")
    assert backend.data == {("irisfs-test", "p1"): "pw"}
    assert store.get("p1") == "pw"
    store.delete("p1")
    store.delete("p1")  # deleting a missing secret is not an error
    assert store.get("p1") is None


def test_default_store_uses_keyring_when_usable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secrets, "keyring_usable", lambda: True)
    assert isinstance(secrets.default_store(tmp_path), secrets.KeyringSecretStore)
