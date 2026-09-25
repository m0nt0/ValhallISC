import pytest

from irisfs.atelier.models import DatabaseInfo, DocInfo, NamespaceInfo
from irisfs.vfs.filters import is_visible

USER = NamespaceInfo(
    "USER",
    (
        DatabaseInfo("USER", True, False),
        DatabaseInfo("IRISLIB", False, True),
        DatabaseInfo("ENSLIB", False, True),
        DatabaseInfo("APPCODE", False, False),
    ),
)
SYS = NamespaceInfo("%SYS", (DatabaseInfo("IRISSYS", True, True), DatabaseInfo("IRISLIB", False, True)))


def doc(name: str, db: str = "USER", upd: bool = True, gen: bool = False) -> DocInfo:
    return DocInfo(name=name, cat="CLS", ts="2026-01-01 00:00:00.000", db=db, upd=upd, gen=gen)


@pytest.mark.parametrize(
    ("d", "visible"),
    [
        (doc("Demo.Person.cls"), True),
        (doc("App.X.cls", db="APPCODE"), True),  # mapped from a non-system db
        (doc("%Library.String.cls", db="IRISLIB"), False),
        (doc("CSPX.Dashboard.Page.cls", db="ENSLIB"), False),
        (doc("INFORMATION.SCHEMA.VIEWS.cls", db="IRISLIB"), False),
        (doc("%Z.Mine.cls"), False),  # % names are system by convention
        (doc("EnsJob.mac", upd=False), False),  # Ens-generated routine in the user db
        (doc("Ensemble.inc"), False),
        (doc("EnsebXMLErrors.inc"), False),
        (doc("Demo.Rtn2.mac", upd=False), True),  # upd = "up to date" (compiled), not "updatable"
        (doc("Enstrophy.Calc.cls"), True),  # only the reserved Ens prefixes are hidden
        (doc("Demo.Gen.cls", gen=True), False),
        (doc("DemoTable.LUT", db="@OTHER"), True),
        (doc("HIPAA_4010.X12", db="@OTHER"), False),
        (doc("EnsLib.Printing.Dispatcher.bpl", db="@OTHER"), False),
        (doc("/csp/user/menu.csp", db="@FS"), False),
    ],
)
def test_default_filter(d: DocInfo, visible: bool) -> None:
    assert is_visible(d, USER, show_system=False) is visible


def test_show_system_shows_everything_but_csp() -> None:
    assert is_visible(doc("%Library.String.cls", db="IRISLIB"), USER, show_system=True)
    assert not is_visible(doc("/csp/user/menu.csp", db="@FS"), USER, show_system=True)


def test_percent_sys_default_db_items_visible() -> None:
    assert is_visible(doc("SYS.Database.cls", db="IRISSYS"), SYS, show_system=False)
    assert not is_visible(doc("%SYS.Task.cls", db="IRISSYS"), SYS, show_system=False)
