"""Contract tests for MemoryVectorConnector."""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings

from hara.config.schemas import MemoryVectorConfig
from hara.connectors.testing import VectorConnectorContractTests
from hara.connectors.vector.memory import MemoryVectorConnector


class TestMemoryVectorConnector(VectorConnectorContractTests):
    @pytest.fixture
    async def connector(self, fake_embedder: Embeddings) -> MemoryVectorConnector:
        return MemoryVectorConnector.from_config(
            MemoryVectorConfig(type="memory"), embedder=fake_embedder
        )
