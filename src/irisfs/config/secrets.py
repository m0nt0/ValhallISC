"""Password storage: OS keyring when usable, otherwise a 0600 JSON file (with a logged warning)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
SERVICE = "ValhallISC"


class SecretStore(Protocol):
    description: str

    def get(self, profile_id: str) -> str | None: ...
    def set(self, profile_id: str, password: str) -> None: ...
    def delete(self, profile_id: str) -> None: ...


class KeyringSecretStore:
    def __init__(self, service: str = SERVICE) -> None:
        import keyring

        self._keyring = keyring
        self.service = service
        backend = keyring.get_keyring()
        self.description = f"keyring ({backend.__class__.__module__}.{backend.__class__.__name__})"

    def get(self, profile_id: str) -> str | None:
        value = self._keyring.get_password(self.service, profile_id)
        return None if value is None else str(value)

    def set(self, profile_id: str, password: str) -> None:
        self._keyring.set_password(self.service, profile_id, password)

    def delete(self, profile_id: str) -> None:
        from keyring.errors import PasswordDeleteError

        with contextlib.suppress(PasswordDeleteError):
            self._keyring.delete_password(self.service, profile_id)


class FileSecretStore:
    """Fallback for systems without a usable keyring (e.g. headless Linux). Not encrypted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.description = f"file {path} (unencrypted, mode 0600)"
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("secret file %s unreadable; starting empty", self.path)
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def get(self, profile_id: str) -> str | None:
        with self._lock:
            return self._read().get(profile_id)

    def set(self, profile_id: str, password: str) -> None:
        with self._lock:
            data = self._read()
            data[profile_id] = password
            self._write(data)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(profile_id, None) is not None:
                self._write(data)


def keyring_usable() -> bool:
    """False for keyring's fail/null/chainer-without-backends setups (typical in containers)."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
    except Exception:  # keyring import/backends can fail in many platform-specific ways
        return False
    if isinstance(backend, fail.Keyring) or "null" in backend.__class__.__module__:
        return False
    if backend.__class__.__name__ == "ChainerBackend":
        return bool(getattr(backend, "backends", []))
    return True


def default_store(config_dir: Path) -> SecretStore:
    if keyring_usable():
        return KeyringSecretStore()
    store = FileSecretStore(config_dir / "secrets.json")
    log.warning("no usable OS keyring; passwords are stored in %s", store.path)
    return store
