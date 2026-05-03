"""Contract tests for SQLiteConnector (file-backed)."""

from __future__ import annotations

import pytest

from hara.config.schemas import SQLiteSQLConfig
from hara.connectors.sql.sqlite import SQLiteConnector
from hara.connectors.testing import SQLConnectorContractTests


class TestSQLiteConnector(SQLConnectorContractTests):
    @pytest.fixture
    async def connector(self, tmp_path) -> SQLiteConnector:
        db_path = tmp_path / "test.db"
        return SQLiteConnector.from_config(SQLiteSQLConfig(type="sqlite", path=db_path))
