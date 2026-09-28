"""Placing mapped entries from the namespace's mapping table (irisfs.vfs.mapping)."""

from __future__ import annotations

from irisfs.atelier.models import DatabaseInfo, NamespaceInfo, NamespaceMappings
from irisfs.vfs import mapping

MAPS = NamespaceMappings(
    packages=(("CSPX.Dashboard", "ENSLIB"), ("Shared", "CODE"), ("Shared.Legacy", "OLDCODE")),
    routines=(("tk*", "", "CODE"), ("tkSys*", "", "ENSLIB"), ("tkExact", "MAC", "OLDCODE"), ("A:M", "", "X")),
)
INFO = NamespaceInfo(
    "APP",
    (
        DatabaseInfo("APP", True, False),
        DatabaseInfo("CODE", False, False),
        DatabaseInfo("OLDCODE", False, False),
        DatabaseInfo("ENSLIB", False, True),
    ),
)


def test_package_mapping_most_specific_wins() -> None:
    assert mapping.package_database(MAPS, "Shared") == "CODE"
    assert mapping.package_database(MAPS, "Shared.Util.Thing") == "CODE"
    assert mapping.package_database(MAPS, "Shared.Legacy.Old") == "OLDCODE"
    assert mapping.package_database(MAPS, "SharedX") is None  # a prefix of the name is not a package
    assert mapping.package_database(MAPS, "CSPX") is None  # only a sub-package is mapped
    assert mapping.databases_inside(MAPS, "CSPX") == ["ENSLIB"]


def test_routine_mapping_exact_beats_longer_wildcard_beats_shorter() -> None:
    assert mapping.routine_database(MAPS, "tkimport", "inc") == "CODE"
    assert mapping.routine_database(MAPS, "tkSysThing", "mac") == "ENSLIB"
    assert mapping.routine_database(MAPS, "tkExact", "mac") == "OLDCODE"
    assert mapping.routine_database(MAPS, "tkExact", "inc") == "CODE"  # the exact mapping is for MAC only
    assert mapping.routine_database(MAPS, "zzz", "mac") is None


def test_ranges_are_not_guessed() -> None:
    assert mapping.routine_database(MAPS, "Bravo", "mac") is None


def test_database_rule() -> None:
    assert mapping.database_visible(INFO, "CODE") is True
    assert mapping.database_visible(INFO, "APP") is True
    assert mapping.database_visible(INFO, "enslib") is False
    assert mapping.database_visible(INFO, "ELSEWHERE") is None
