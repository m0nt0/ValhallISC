"""ValhallISC: mount InterSystems IRIS code as a local filesystem (Python package: irisfs)."""

__version__ = "0.9.1"


def _git_sha() -> str:
    """irisfs/_build.py is written by the build scripts; absent when running from source."""
    from importlib import import_module

    try:
        return str(getattr(import_module("irisfs._build"), "GIT_SHA", ""))
    except ImportError:
        return ""


GIT_SHA = _git_sha()
APP_NAME = "ValhallISC"  # shown to users; also names the config/log folders and keyring service
CLI_NAME = "valhallisc"
