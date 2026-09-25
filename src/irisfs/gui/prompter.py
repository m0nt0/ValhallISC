"""User prompts behind an interface, so the controller can be tested without wx."""

from __future__ import annotations

from typing import Protocol


class Prompter(Protocol):
    def confirm(self, title: str, message: str, *, yes: str = "Yes", no: str = "No") -> bool: ...
    def error(self, title: str, message: str) -> None: ...
    def info(self, title: str, message: str) -> None: ...
    def notify(self, title: str, message: str) -> None:
        """Non-blocking notification (system notification where available)."""
        ...
