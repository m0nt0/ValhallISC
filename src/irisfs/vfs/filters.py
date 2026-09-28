"""Which documents are shown when "show system items" is off (doc/DECISIONS.md ADR-003)."""

from __future__ import annotations

import re

from irisfs.atelier.models import DocInfo, NamespaceInfo

# Names reserved by InterSystems: %*, the interoperability (Ens*) and HIPAA schema documents. Ens-generated
# routines (EnsJob.mac, ...) live in the user database, so the database test alone does not hide them.
_SYSTEM_NAME = re.compile(r"^(%|Ens(?:[A-Z.\-]|emble|eb)|HIPAA_)")


def is_system_name(name: str) -> bool:
    """True for a name reserved by InterSystems. For a package, pass its name with a trailing dot
    ("Ens."): the test is a prefix test, so it then holds for every document inside the package."""
    return bool(_SYSTEM_NAME.match(name))


def is_visible(doc: DocInfo, ns: NamespaceInfo, *, show_system: bool) -> bool:
    if doc.name.startswith("/") or doc.db == "@FS":
        return False  # CSP/web files are not exposed in v1
    if show_system:
        return True
    if doc.gen or _SYSTEM_NAME.match(doc.name):
        return False
    if doc.db == "@OTHER":
        return True
    if doc.db.startswith("@"):
        return False
    if doc.db == ns.default_db:
        return True
    return not ns.is_system_db(doc.db)
