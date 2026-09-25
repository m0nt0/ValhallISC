import subprocess
import sys

import pytest

from irisfs import __version__
from irisfs.cli import build_parser, main

COMMANDS = ["gui", "mount", "unmount", "worker", "profiles", "doctor"]


@pytest.mark.parametrize("command", COMMANDS)
def test_every_subcommand_has_help(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_parser_knows_all_commands() -> None:
    _, handlers = build_parser()
    assert set(handlers) == set(COMMANDS)


def test_doctor_runs_as_module() -> None:
    r = subprocess.run([sys.executable, "-m", "irisfs", "doctor"], capture_output=True, text=True, timeout=60)
    assert "ValhallISC" in r.stdout
    assert "fuse:" in r.stdout
    assert r.returncode in (0, 1), r.stderr
