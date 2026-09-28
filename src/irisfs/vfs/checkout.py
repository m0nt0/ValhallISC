"""How a document's source control state is shown in file managers (ADR-017).

  * macOS: Finder tags, served as the `com.apple.metadata:_kMDItemUserTags` extended attribute (a binary
    plist of "name\\n<color>"): green "Checked out" by you, orange "Checked out by <user>" by someone else.
  * Linux: the freedesktop `user.xdg.tags` / `user.xdg.comment` attributes (KDE Dolphin shows them; they
    have no colour), plus the list file below.
  * Windows, and Linux file managers that ignore those attributes: a read-only `_CHECKED_OUT.txt` in each
    folder that holds checked-out documents.
A document the source control says is not editable is shown read-only everywhere.
"""

from __future__ import annotations

import plistlib

from irisfs.atelier.models import SourceStatus

MAC_TAGS = "com.apple.metadata:_kMDItemUserTags"
XDG_TAGS = "user.xdg.tags"
XDG_COMMENT = "user.xdg.comment"
LIST_FILE = "_CHECKED_OUT.txt"

# Finder tag colours: 0 none, 1 grey, 2 green, 3 purple, 4 blue, 5 yellow, 6 red, 7 orange
_GREEN, _ORANGE = 2, 7


def by_me(status: SourceStatus, user: str) -> bool:
    return status.checked_out_by.casefold() == user.casefold()


def label(status: SourceStatus, user: str) -> str | None:
    """The tag text for a document, or None when it isn't checked out."""
    if not status.checked_out:
        return None
    if not status.checked_out_by or by_me(status, user):
        return "Checked out"
    return f"Checked out by {status.checked_out_by}"


def mac_tags(status: SourceStatus, user: str) -> bytes | None:
    text = label(status, user)
    if text is None:
        return None
    colour = _GREEN if text == "Checked out" else _ORANGE
    return plistlib.dumps([f"{text}\n{colour}"], fmt=plistlib.FMT_BINARY)


def attribute_names(style: str) -> list[str]:
    if style == "macos":
        return [MAC_TAGS]
    if style == "xdg":
        return [XDG_TAGS, XDG_COMMENT]
    return []


def attribute(style: str, name: str, status: SourceStatus, user: str) -> bytes | None:
    """The value of extended attribute `name` in the given style, or None if the document has none."""
    if style == "macos" and name == MAC_TAGS:
        return mac_tags(status, user)
    if style == "xdg" and name in (XDG_TAGS, XDG_COMMENT):
        text = label(status, user)
        return text.encode("utf-8") if text else None
    return None


def list_file(entries: list[tuple[str, SourceStatus]], user: str) -> bytes:
    """Content of `_CHECKED_OUT.txt`: one line per checked-out document of the folder."""
    lines = [
        "Documents in this folder checked out in the server's source control:",
        "",
    ]
    for filename, status in sorted(entries):
        who = "you" if by_me(status, user) or not status.checked_out_by else status.checked_out_by
        lines.append(f"{filename}\tchecked out by {who}")
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")
