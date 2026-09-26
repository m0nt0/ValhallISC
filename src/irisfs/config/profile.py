"""Connection profile: one IRIS server mounted at one local directory."""

from __future__ import annotations

import ntpath
import posixpath
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any

DEFAULT_PORT = 52773
_HOST_RE = re.compile(r"^[A-Za-z0-9._\-:\[\]]+$")  # hostname, IPv4, or [IPv6]
_FLAGS_RE = re.compile(r"^[A-Za-z0-9\-/]*$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:\\?$")


class ProfileValidationError(ValueError):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


@dataclass
class Profile:
    name: str
    host: str
    username: str
    mount_point: str
    port: int = DEFAULT_PORT
    https: bool = False
    verify_tls: bool = True
    path_prefix: str = ""
    read_only: bool = False
    show_system: bool = False
    compile_on_import: bool = True
    compile_flags: str = "cuk"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def base_url(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://{self.host}:{self.port}{self.path_prefix}"

    def validate(self, system: str) -> dict[str, str]:
        """Field-level errors (empty dict when valid). `system` is platform.system()."""
        errors: dict[str, str] = {}
        if not self.name.strip():
            errors["name"] = "Name is required"
        elif len(self.name) > 64:
            errors["name"] = "Name must be at most 64 characters"
        if not self.host.strip():
            errors["host"] = "Server is required"
        elif "://" in self.host or not _HOST_RE.match(self.host):
            errors["host"] = "Enter a host name or IP address (no http://, no path)"
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            errors["port"] = "Port must be between 1 and 65535"
        if not self.username.strip():
            errors["username"] = "User is required"
        if self.path_prefix and (not self.path_prefix.startswith("/") or self.path_prefix.endswith("/")):
            errors["path_prefix"] = "Prefix must start with '/' and not end with '/' (e.g. /iris)"
        if not _FLAGS_RE.match(self.compile_flags):
            errors["compile_flags"] = "Invalid compile flags"
        mp = self.mount_point.strip()
        if not mp:
            errors["mount_point"] = "Mount directory is required"
        elif system == "Windows":
            drive = ntpath.splitdrive(mp)[0]
            local_abs = ntpath.isabs(mp) and len(drive) == 2 and drive[1] == ":"  # not UNC
            if not (_DRIVE_RE.match(mp) or local_abs):
                errors["mount_point"] = "Use a drive letter (X:) or an absolute path (C:\\...)"
        elif not posixpath.isabs(mp):
            errors["mount_point"] = "Mount directory must be an absolute path"
        return errors

    def check(self, system: str) -> None:
        errors = self.validate(system)
        if errors:
            raise ProfileValidationError(errors)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        """Build from stored data, ignoring unknown keys (written by newer versions)."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})
