"""Contract tests for PostgresConnector. Requires Docker."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from hara.config.schemas import PostgresSQLConfig
from hara.connectors.sql.postgres import PostgresConnector
from hara.connectors.testing import SQLConnectorContractTests


@pytest.mark.integration
class TestPostgresConnector(SQLConnectorContractTests):
    @pytest.fixture
    async def connector(self, postgres_url: str) -> PostgresConnector:
        cfg = PostgresSQLConfig(type="postgres", url=SecretStr(postgres_url))
        return PostgresConnector.from_config(cfg)
