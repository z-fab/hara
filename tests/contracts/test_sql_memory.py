"""Contract tests for MemorySQLConnector."""

from __future__ import annotations

import pytest

from hara.config.schemas import MemorySQLConfig
from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import SQLConnectorContractTests


class TestMemorySQLConnector(SQLConnectorContractTests):
    @pytest.fixture
    async def connector(self) -> MemorySQLConnector:
        return MemorySQLConnector.from_config(MemorySQLConfig(type="memory"))
