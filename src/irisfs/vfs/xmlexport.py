"""Validation of files copied into the mount: they must be IRIS XML export files."""

from __future__ import annotations

import xml.etree.ElementTree as StdET
from dataclasses import dataclass

import defusedxml.ElementTree as ET
from defusedxml import DefusedXmlException

# Elements IRIS writes in code exports. <Global> (data) and anything else is refused on purpose:
# copying a file into a code folder must never load arbitrary global data.
_CODE_ITEMS = {"Class", "Routine", "Document"}
_ALLOWED = _CODE_ITEMS | {"Project", "CSP", "CSPBase64"}


class InvalidExport(ValueError):
    pass


@dataclass(frozen=True)
class ExportManifest:
    items: tuple[str, ...]  # document names, e.g. "Demo.Person.cls", "DEMORTN.mac"


def _doc_name(el: StdET.Element) -> str | None:
    name = el.get("name")
    if not name:
        return None
    if el.tag == "Class":
        return f"{name}.cls"
    if el.tag == "Routine":
        return f"{name}.{(el.get('type') or 'MAC').lower()}"
    if el.tag == "Document":
        return name
    return None


def validate(data: bytes) -> ExportManifest:
    """Parse safely (no DTDs/entities) and check this is an IRIS export with at least one code item."""
    if not data.strip():
        raise InvalidExport("The file is empty")
    try:
        root = ET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise InvalidExport(f"Refused unsafe XML ({e.__class__.__name__})") from e
    except StdET.ParseError as e:
        raise InvalidExport(f"Not well-formed XML: {e}") from e
    if root.tag != "Export":
        raise InvalidExport(f"Not an IRIS export: root element is <{root.tag}>, expected <Export>")
    unexpected = sorted({el.tag for el in root if el.tag not in _ALLOWED})
    if unexpected:
        raise InvalidExport(f"Export contains unsupported elements: {', '.join(unexpected)}")
    names = tuple(n for n in (_doc_name(el) for el in root if el.tag in _CODE_ITEMS) if n)
    if not names:
        raise InvalidExport("The export contains no classes, routines or documents")
    return ExportManifest(names)
