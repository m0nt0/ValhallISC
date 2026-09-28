"""Source control state on a real mount (ADR-017), with a fake source control class installed in USER.

The fake says: Demo.Person is checked out by the mounting user, Demo.Util by "mario" (so not editable for
us), Demo.Big is not checked out and not editable; everything else is editable. Registering a source
control class needs %SYS, reached through `docker compose exec`: without it (e.g. the Linux run inside
Docker) the test is skipped.
"""

from __future__ import annotations

import contextlib
import os
import platform
import plistlib
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.conftest import ROOT, IrisConn
from tests.e2e.conftest import MountedFs

pytestmark = [pytest.mark.iris, pytest.mark.fuse]

SC_CLASS = "Irisfs.E2ESourceControl"
SC_SOURCE = [
    f"Class {SC_CLASS} Extends %Studio.SourceControl.Base",
    "{",
    "Method GetStatus(InternalName As %String, ByRef IsInSourceControl As %Boolean, "
    "ByRef Editable As %Boolean, ByRef IsCheckedOut As %Boolean, ByRef UserCheckedOut As %String) As %Status",
    "{",
    '  Set IsInSourceControl = 1, Editable = 1, IsCheckedOut = 0, UserCheckedOut = ""',
    '  If InternalName = "Demo.Person.CLS" { Set IsCheckedOut = 1, UserCheckedOut = $Username }',
    '  If InternalName = "Demo.Util.CLS" { Set IsCheckedOut = 1, UserCheckedOut = "mario", Editable = 0 }',
    '  If InternalName = "Demo.Big.CLS" { Set Editable = 0 }',
    "  Quit $$$OK",
    "}",
    "}",
]


def iris_sys(script: str) -> None:
    compose = ["docker", "compose", "-f", str(ROOT / "docker" / "docker-compose.yml")]
    subprocess.run(
        [*compose, "exec", "-T", "iris", "iris", "session", "IRIS", "-U", "%SYS"],
        input=script + "\nHalt\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )


def install(iris: IrisConn) -> None:
    """Register the fake source control class in USER (needs `docker compose exec`)."""
    iris_sys('Kill ^%SYS("SourceControlClass","USER")')  # compiling needs it unregistered
    base = f"http://{iris.host}:{iris.port}/api/atelier/v8/USER"
    with httpx.Client(auth=(iris.user, iris.password), timeout=30) as http:
        doc = {"enc": False, "content": SC_SOURCE}
        http.put(f"{base}/doc/{SC_CLASS}.cls", params={"ignoreConflict": 1}, json=doc)
        body = http.post(f"{base}/action/compile", json=[f"{SC_CLASS}.cls"]).json()
        assert not body["status"]["errors"], body["status"]["errors"]
    iris_sys(f'Set ^%SYS("SourceControlClass","USER")="{SC_CLASS}"')


def uninstall(iris: IrisConn) -> None:
    iris_sys('Kill ^%SYS("SourceControlClass","USER")')
    base = f"http://{iris.host}:{iris.port}/api/atelier/v8/USER"
    with (
        httpx.Client(auth=(iris.user, iris.password), timeout=30) as http,
        contextlib.suppress(httpx.HTTPError),
    ):
        http.delete(f"{base}/doc/{SC_CLASS}.cls")


@pytest.fixture
def fake_source_control(iris_conn: IrisConn) -> Iterator[None]:
    if os.environ.get("IRISFS_E2E_SOURCE_CONTROL") == "preinstalled":
        yield  # registered from outside, e.g. for the Linux run inside Docker
        return
    try:
        install(iris_conn)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("needs `docker compose exec` into the test IRIS to register a source control class")
    try:
        yield
    finally:
        uninstall(iris_conn)


def finder_tags(path: Path) -> list[str]:
    """Tags as Finder reads them (NSURLTagNamesKey), not just the raw attribute."""
    from Foundation import NSURL

    ok, value, _error = NSURL.fileURLWithPath_(str(path)).getResourceValue_forKey_error_(
        None, "NSURLTagNamesKey", None
    )
    assert ok
    return list(value or [])


def test_checkout_state_on_a_real_mount(
    fake_source_control: None, mount_factory: Callable[..., MountedFs]
) -> None:
    m = mount_factory()
    demo = m.path / "USER" / "Demo"
    names = os.listdir(demo)
    person, util, big, unicode_ = (demo / f"{n}.cls.xml" for n in ("Person", "Util", "Big", "Unicode"))

    assert oct(person.stat().st_mode & 0o777) == oct(0o644)
    assert oct(util.stat().st_mode & 0o777) == oct(0o444)  # checked out by someone else
    assert oct(big.stat().st_mode & 0o777) == oct(0o444)  # not checked out: not editable
    assert oct(unicode_.stat().st_mode & 0o777) == oct(0o644)
    assert person.read_bytes().startswith(b"<?xml")  # reading is never restricted
    with pytest.raises(PermissionError):
        big.open("r+b").close()

    if platform.system() == "Darwin":
        assert finder_tags(person) == ["Checked out"]
        assert finder_tags(util) == ["Checked out by mario"]
        assert finder_tags(big) == []
        raw = subprocess.run(
            ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(util)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert plistlib.loads(bytes.fromhex(raw.replace("\n", "").replace(" ", ""))) == [
            "Checked out by mario\n7"
        ]
        assert "_CHECKED_OUT.txt" not in names
    else:  # Linux
        assert os.getxattr(util, "user.xdg.tags") == b"Checked out by mario"
        listing = (demo / "_CHECKED_OUT.txt").read_text()
        assert "Person.cls.xml\tchecked out by you" in listing
        assert "Util.cls.xml\tchecked out by mario" in listing
