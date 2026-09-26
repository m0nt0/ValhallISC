"""Files that operating systems and file managers create on their own. They live only in memory."""

from __future__ import annotations

_EXACT = {
    ".DS_Store",
    ".localized",
    ".hidden",
    ".metadata_never_index",
    ".metadata_never_index_unless_rootfs",
    ".metadata_direct_scope_only",
    ".VolumeIcon.icns",
    "desktop.ini",
    "Desktop.ini",
    "Thumbs.db",
    "autorun.inf",
    ".directory",  # KDE
}
_PREFIXES = ("._", ".~lock.", ".goutputstream-", "~$")


def is_junk(filename: str) -> bool:
    return filename in _EXACT or filename.startswith(_PREFIXES)
