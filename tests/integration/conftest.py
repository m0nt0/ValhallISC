from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from irisfs.atelier.api import AtelierApi
from irisfs.atelier.client import AtelierClient
from tests.conftest import IrisConn
from tests.fakes.fake_atelier import FakeAtelier


@pytest.fixture
def real_client(iris_conn: IrisConn) -> Iterator[AtelierClient]:
    with AtelierClient(iris_conn.base_url, iris_conn.user, iris_conn.password) as c:
        yield c


@pytest.fixture(params=["fake", pytest.param("real", marks=pytest.mark.iris)])
def api(request: pytest.FixtureRequest) -> Iterator[AtelierApi]:
    """The same contract runs against the in-memory fake and the real IRIS container."""
    if request.param == "fake":
        fake = FakeAtelier()
        fake.add_namespace("USER")
        fake.add_namespace("%SYS")
        yield fake
    else:
        yield request.getfixturevalue("real_client")


@pytest.fixture
def unique() -> str:
    return "C" + uuid.uuid4().hex[:10]
