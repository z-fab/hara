"""Shared fixtures for contract tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """Spin up a Postgres container per test module.

    testcontainers' `get_connection_url(driver="asyncpg")` returns a
    SQLAlchemy-flavored URL (`postgresql+asyncpg://...`) which asyncpg's
    own `connect()` rejects with ClientConfigurationError. We strip the
    `+asyncpg` suffix to get a plain `postgresql://` URL that asyncpg parses.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        url = pg.get_connection_url(driver="asyncpg").replace("+asyncpg", "")
        yield url
