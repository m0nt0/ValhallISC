"""Shared fixtures.

Tests needing external resources carry markers (iris, fuse, gui). They are skipped when the resource
is missing, UNLESS the matching IRISFS_REQUIRE_* variable is set: gate runs set it so that a missing
dependency fails loudly instead of silently skipping.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "docker" / ".env.test"


def _env_defaults() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def setting(name: str) -> str:
    return os.environ.get(name) or _env_defaults().get(name, "")


def _required(kind: str) -> bool:
    return os.environ.get(f"IRISFS_REQUIRE_{kind}") == "1"


def _unavailable(kind: str, reason: str) -> None:
    if _required(kind):
        pytest.fail(f"IRISFS_REQUIRE_{kind}=1 but {reason}", pytrace=False)
    pytest.skip(reason)


@dataclass(frozen=True)
class IrisConn:
    host: str
    port: int
    user: str
    password: str
    ro_user: str
    ro_password: str

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


_iris_state: dict[str, object] = {}


@pytest.fixture(scope="session")
def iris_conn() -> IrisConn:
    conn = IrisConn(
        host=setting("IRISFS_TEST_HOST") or "localhost",
        port=int(setting("IRISFS_TEST_PORT") or 52773),
        user=setting("IRISFS_TEST_USER"),
        password=setting("IRISFS_TEST_PASSWORD"),
        ro_user=setting("IRISFS_TEST_RO_USER"),
        ro_password=setting("IRISFS_TEST_RO_PASSWORD"),
    )
    if "ok" not in _iris_state:
        try:
            r = httpx.get(f"{conn.base_url}/api/atelier/", auth=(conn.user, conn.password), timeout=5)
            _iris_state["ok"] = r.status_code == 200
            _iris_state["why"] = f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            _iris_state["ok"] = False
            _iris_state["why"] = str(e)
    if not _iris_state["ok"]:
        _unavailable("IRIS", f"test IRIS not reachable at {conn.base_url} ({_iris_state['why']})")
    return conn


@pytest.fixture(scope="session")
def fuse_available() -> None:
    from irisfs.mount import fuselib

    try:
        fuselib.ensure_loaded()
    except fuselib.FuseNotFoundError as e:
        _unavailable("FUSE", f"FUSE not available: {e}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Marker-driven fixtures: tests marked iris/fuse get the availability check automatically.
    for item in items:
        if item.get_closest_marker("iris") and "iris_conn" not in getattr(item, "fixturenames", []):
            item.fixturenames.append("iris_conn")  # type: ignore[attr-defined]
        if item.get_closest_marker("fuse") and "fuse_available" not in getattr(item, "fixturenames", []):
            item.fixturenames.append("fuse_available")  # type: ignore[attr-defined]
