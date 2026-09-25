"""Bijective mapping between IRIS document names and relative file paths (layout like VS Code isfs).

    Demo.Sub.Thing.cls  <->  ("Demo", "Sub", "Thing.cls.xml")
    DEMORTN.mac         <->  ("DEMORTN.mac.xml",)
    DemoTable.LUT       <->  ("DemoTable.LUT.xml",)

Package segments become directories; the file keeps the item's extension plus ".xml" because its
content is an XML export. CSP paths ("/csp/...") are not mapped in v1.
"""

from __future__ import annotations

import re

SUFFIX = ".xml"
_SEGMENT_RE = re.compile(r"^[^./\\\x00-\x1f]+$")  # non-empty, no dots, no separators, no control chars
_EXT_RE = re.compile(r"^[A-Za-z0-9]+$")


def doc_to_path(name: str) -> tuple[str, ...] | None:
    """Relative path parts for a document name, or None if the name cannot be mapped."""
    base, dot, ext = name.rpartition(".")
    if not dot or not base or not _EXT_RE.match(ext):
        return None
    segments = base.split(".")
    if not all(_SEGMENT_RE.match(s) for s in segments):
        return None
    return (*segments[:-1], f"{segments[-1]}.{ext}{SUFFIX}")


def path_to_doc(parts: tuple[str, ...]) -> str | None:
    """Document name for relative path parts, or None if the path is not a canonical document path."""
    if not parts or not parts[-1].endswith(SUFFIX):
        return None
    stem = parts[-1][: -len(SUFFIX)]
    name = ".".join((*parts[:-1], stem))
    return name if doc_to_path(name) == parts else None


def is_xml_name(filename: str) -> bool:
    return filename.lower().endswith(SUFFIX) and len(filename) > len(SUFFIX)
