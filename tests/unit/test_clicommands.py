"""G11: headless CLI (profile management, status, error handling) without real mounts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from irisfs.cli import main
from irisfs.clicommands import rewrite_aliases
from irisfs.mount import fuselib, registry


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VALHALLISC_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("VALHALLISC_SECRETS", "file")  # never touch the real keychain
    monkeypatch.delenv("VALHALLISC_PASSWORD", raising=False)
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = main(["--batch", *argv])
    out = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(out[-1]) if out else {}


def create(capsys: pytest.CaptureFixture[str], name: str = "dev", *extra: str) -> tuple[int, dict[str, Any]]:
    os.environ["VALHALLISC_PASSWORD"] = "pw"
    try:
        return run(capsys, "--create-profile", name, "--host", "iris.local", "--user", "_SYSTEM", *extra)
    finally:
        del os.environ["VALHALLISC_PASSWORD"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            ["--batch", "--create-profile", "X", "--host", "h"],
            ["--batch", "profile", "create", "X", "--host", "h"],
        ),
        (["--create-profile=X", "--batch"], ["--batch", "profile", "create", "X"]),
        (["--connect", "X"], ["connect", "X"]),
        (["--disconnect", "X", "--force"], ["disconnect", "X", "--force"]),
        (["--list-profiles", "--batch"], ["--batch", "profile", "list"]),
        (["--status"], ["status"]),
        (["-v", "--test", "X"], ["-v", "test", "X"]),
        (["profile", "list", "--batch"], ["--batch", "profile", "list"]),  # --batch anywhere
        (["doctor"], ["doctor"]),
    ],
)
def test_rewrite_aliases(argv: list[str], expected: list[str]) -> None:
    assert rewrite_aliases(argv) == expected


def test_create_list_show(capsys: pytest.CaptureFixture[str], isolated: Path) -> None:
    code, data = create(capsys, "dev", "--port", "1972", "--read-only", "--mountpoint", str(isolated / "m"))
    assert code == 0 and data["ok"] and data["profile"]["port"] == 1972 and data["profile"]["read_only"]
    assert data["profile"]["has_password"] and "password" not in json.dumps(data).replace("has_password", "")
    code, data = run(capsys, "--list-profiles")
    assert code == 0 and [p["name"] for p in data["profiles"]] == ["dev"]
    code, data = run(capsys, "--show-profile", "DEV")  # names are case-insensitive
    assert code == 0 and data["profile"]["connected"] is False


def test_default_mountpoint(capsys: pytest.CaptureFixture[str]) -> None:
    _, data = create(capsys, "Local")
    assert data["profile"]["mount_point"].endswith(os.path.join("ValhallISC", "Local"))


def test_create_duplicate_and_invalid(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev")
    code, data = create(capsys, "dev")
    assert code == 2 and data["error"] == "exists"
    code, data = create(capsys, "bad", "--port", "0", "--prefix", "iris")
    assert code == 2 and set(data["fields"]) == {"port", "path_prefix"}


def test_update_rename_and_flags(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev")
    code, data = run(
        capsys, "--update-profile", "dev", "--rename", "prod", "--no-compile", "--https", "--port", "443"
    )
    p = data["profile"]
    assert code == 0 and (p["name"], p["compile_on_import"], p["https"], p["port"]) == (
        "prod",
        False,
        True,
        443,
    )
    assert p["has_password"]  # kept


def test_update_password_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    create(capsys, "dev")
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("new-secret\n"))
    code, _ = run(capsys, "profile", "update", "dev", "--password-stdin")
    assert code == 0
    secrets = json.loads((isolated / "cfg" / "secrets.json").read_text())
    assert list(secrets.values()) == ["new-secret"]


def test_not_found_everywhere(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ["--show-profile", "x"],
        ["--update-profile", "x"],
        ["--delete-profile", "x"],
        ["--connect", "x"],
        ["--disconnect", "x"],
        ["--test", "x"],
        ["status", "x"],
    ):
        code, data = run(capsys, *argv)
        assert code == 5 and data["error"] == "not_found", argv


def test_delete(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev")
    code, data = run(capsys, "--delete-profile", "dev")
    assert code == 0 and data["deleted"] == "dev"
    assert run(capsys, "--list-profiles")[1]["profiles"] == []


def test_connected_profile_cannot_be_deleted_and_shows_in_status(
    capsys: pytest.CaptureFixture[str], isolated: Path
) -> None:
    _, data = create(capsys, "dev")
    entry = registry.new_entry(data["profile"]["id"], "dev", "/mnt/dev", "cli")  # our own pid: alive
    registry.write(isolated / "cfg" / "mounts", entry)
    code, data = run(capsys, "--delete-profile", "dev")
    assert code == 6 and data["error"] == "connected"
    code, data = run(capsys, "--status")
    assert data["profiles"] == [
        {
            "profile": "dev",
            "connected": True,
            "mountpoint": data["profiles"][0]["mountpoint"],
            "pid": os.getpid(),
            "owner": "cli",
        }
    ]
    code, data = run(capsys, "--connect", "dev")
    assert code == 0 and data["already"] is True


def test_disconnect_when_not_connected_is_ok(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev")
    code, data = run(capsys, "--disconnect", "dev")
    assert code == 0 and data["already"] is True


def test_connect_without_fuse(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    create(capsys, "dev")
    monkeypatch.setattr(fuselib, "find_library", lambda system=None: None)
    code, data = run(capsys, "--connect", "dev")
    assert code == 4 and data["error"] == "FUSE_MISSING"


def test_connect_without_password(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fuselib, "find_library", lambda system=None: fuselib.FuseLibrary("libfuse3", "x"))
    run(capsys, "--create-profile", "nopw", "--host", "h", "--user", "u")
    code, data = run(capsys, "--connect", "nopw")
    assert code == 3 and data["error"] == "no_password"


def test_test_connection_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev", "--port", "9")  # nothing listens on port 9
    code, data = run(capsys, "--test", "dev")
    assert code == 3 and data["ok"] is False


def test_human_output_without_batch(capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys, "dev")
    assert main(["profile", "list"]) == 0
    assert "○ dev: _SYSTEM@iris.local:52773" in capsys.readouterr().out
    assert main(["profile", "show", "missing"]) == 5
    assert "no profile named" in capsys.readouterr().err
