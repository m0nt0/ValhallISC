"""Typed errors raised by the Atelier client."""

from __future__ import annotations


class AtelierError(RuntimeError):
    """Base class. `message` is suitable for showing to a user."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthError(AtelierError):
    """Wrong user name or password (HTTP 401)."""


class ForbiddenError(AtelierError):
    """Authenticated but not allowed (HTTP 403, or IRIS privilege errors)."""


class NotFoundError(AtelierError):
    """Namespace or document does not exist."""


class ServerError(AtelierError):
    """IRIS reported an error (HTTP 5xx or a non-empty status.errors)."""


class ConnectionFailed(AtelierError):
    """The server could not be reached (DNS, refused, TLS, timeout)."""


class UnsupportedServer(AtelierError):
    """The server's Atelier API is too old for the features we need."""
