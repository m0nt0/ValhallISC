"""Profile persistence: a versioned JSON file written atomically. Passwords never go here."""

from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from collections.abc import Callable
from pathlib import Path

import platformdirs

from irisfs import APP_NAME
from irisfs.config.profile import Profile, ProfileValidationError
from irisfs.config.secrets import SecretStore

log = logging.getLogger(__name__)
SCHEMA_VERSION = 1


class ProfileStoreError(RuntimeError):
    pass


class ProfileNotFoundError(KeyError):
    pass


class ProfileActiveError(RuntimeError):
    """Raised when trying to change or delete a profile that is currently mounted."""


def default_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


class ProfileStore:
    def __init__(
        self,
        path: Path,
        *,
        secrets: SecretStore | None = None,
        is_active: Callable[[str], bool] = lambda _id: False,
        system: str | None = None,
    ) -> None:
        self.path = path
        self.secrets = secrets
        self.is_active = is_active
        self.system = system or platform.system()
        self.load_warning: str | None = None
        self._lock = threading.RLock()
        self._profiles: list[Profile] = []
        self.load()

    # ---- persistence -------------------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self.load_warning = None
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._profiles = []
                return
            try:
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("top level is not an object")
                version = int(data.get("version", 1))
                if version > SCHEMA_VERSION:
                    raise ProfileStoreError(
                        f"{self.path} was written by a newer irisfs (schema {version} > {SCHEMA_VERSION}); "
                        "refusing to modify it"
                    )
                self._profiles = [Profile.from_dict(p) for p in data.get("profiles", [])]
            except ProfileStoreError:
                raise
            except (ValueError, TypeError, KeyError) as e:
                backup = self.path.with_name(f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                os.replace(self.path, backup)
                self._profiles = []
                self.load_warning = f"Profile file was unreadable ({e}); it was moved to {backup}"
                log.error(self.load_warning)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "profiles": [p.to_dict() for p in self._profiles]}
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    # ---- queries -----------------------------------------------------------------------------
    def profiles(self) -> list[Profile]:
        with self._lock:
            return [Profile.from_dict(p.to_dict()) for p in self._profiles]

    def get(self, profile_id: str) -> Profile:
        with self._lock:
            for p in self._profiles:
                if p.id == profile_id:
                    return Profile.from_dict(p.to_dict())
        raise ProfileNotFoundError(profile_id)

    def find_by_name(self, name: str) -> Profile | None:
        with self._lock:
            for p in self._profiles:
                if p.name.casefold() == name.casefold():
                    return Profile.from_dict(p.to_dict())
        return None

    # ---- mutations ---------------------------------------------------------------------------
    def _check(self, profile: Profile) -> None:
        errors = profile.validate(self.system)
        other = self.find_by_name(profile.name)
        if other is not None and other.id != profile.id and "name" not in errors:
            errors["name"] = f"A profile named '{other.name}' already exists"
        if errors:
            raise ProfileValidationError(errors)

    def add(self, profile: Profile, password: str | None = None) -> Profile:
        with self._lock:
            if any(p.id == profile.id for p in self._profiles):
                raise ProfileStoreError(f"duplicate profile id {profile.id}")
            self._check(profile)
            self._profiles.append(Profile.from_dict(profile.to_dict()))
            self._save()
            if password is not None and self.secrets is not None:
                self.secrets.set(profile.id, password)
            return self.get(profile.id)

    def update(self, profile: Profile, password: str | None = None) -> Profile:
        """Replace a stored profile. `password=None` keeps the stored password."""
        with self._lock:
            index = next((i for i, p in enumerate(self._profiles) if p.id == profile.id), None)
            if index is None:
                raise ProfileNotFoundError(profile.id)
            if self.is_active(profile.id):
                raise ProfileActiveError(f"'{profile.name}' is mounted; unmount it before editing")
            self._check(profile)
            self._profiles[index] = Profile.from_dict(profile.to_dict())
            self._save()
            if password is not None and self.secrets is not None:
                self.secrets.set(profile.id, password)
            return self.get(profile.id)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            profile = self.get(profile_id)
            if self.is_active(profile_id):
                raise ProfileActiveError(f"'{profile.name}' is mounted; unmount it before deleting")
            self._profiles = [p for p in self._profiles if p.id != profile_id]
            self._save()
            if self.secrets is not None:
                self.secrets.delete(profile_id)

    def password(self, profile_id: str) -> str | None:
        return self.secrets.get(profile_id) if self.secrets is not None else None
