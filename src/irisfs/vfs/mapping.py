"""Which database a mapped package or routine comes from, from the namespace's mapping table (ADR-014).

Pure functions over `NamespaceMappings` (read in %SYS). Every answer is `None` when the table alone can't
tell: the caller then asks IRIS about that entry.
"""

from __future__ import annotations

from irisfs.atelier.models import NamespaceInfo, NamespaceMappings


def package_database(mappings: NamespaceMappings, name: str) -> str | None:
    """Database of the most specific package mapping that covers `name` (a package or a class name)."""
    best: tuple[int, str] | None = None
    for package, db in mappings.packages:
        if (name == package or name.startswith(package + ".")) and (best is None or len(package) > best[0]):
            best = (len(package), db)
    return best[1] if best else None


def databases_inside(mappings: NamespaceMappings, package: str) -> list[str]:
    """Databases of the package mappings strictly below `package` (a folder that only holds those)."""
    return [db for name, db in mappings.packages if name.startswith(package + ".")]


def routine_database(mappings: NamespaceMappings, routine: str, ext: str) -> str | None:
    """Database of the most specific routine mapping for `routine` (name without extension) of type `ext`.

    An exact name beats a wildcard, a longer wildcard prefix beats a shorter one. Range patterns ("A:M") and
    anything else not understood make the answer None rather than a guess."""
    kind = ext.upper()
    best: tuple[int, str] | None = None
    for pattern, rtype, db in mappings.routines:
        wanted = rtype.upper()
        if wanted not in ("", "ALL") and wanted != kind:
            continue
        if ":" in pattern:
            lo, _, hi = pattern.partition(":")
            if lo <= routine <= hi or routine.startswith(hi):
                return None  # a range covers it: its bounds' exact meaning is not worth guessing
            continue
        if pattern.endswith("*"):
            if not routine.startswith(pattern[:-1]):
                continue
            rank = len(pattern) - 1
        elif pattern == routine:
            rank = 1_000_000
        else:
            continue
        if best is None or rank > best[0]:
            best = (rank, db)
    return best[1] if best else None


def database_visible(info: NamespaceInfo, db: str) -> bool | None:
    """ADR-003's database rule: the namespace's own database or a user database is shown, a system one is
    not. None for a database the namespace doesn't list."""
    for d in info.databases:
        if d.name.upper() == db.upper():
            return d.default or not d.dbsys
    return None
