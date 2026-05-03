"""Reusable contract test classes for SQL and Vector connectors.

Third-party connector authors inherit these and implement the `connector`
fixture. All abstract behavior is verified, so a passing test suite is
strong evidence the connector respects the protocol.

Usage:
    class TestMyConnector(VectorConnectorContractTests):
        @pytest.fixture
        async def connector(self) -> VectorConnector:
            return await MyConnector.from_config(my_config, fake_embedder)
"""

from __future__ import annotations

import string

import polars as pl
import pytest
from langchain_core.embeddings import Embeddings

from hara.connectors.sql.base import SQLConnector
from hara.connectors.vector.base import TextChunk, VectorConnector

_PUNCTUATION = frozenset(string.punctuation)
_VOWELS = frozenset("aeiou")


class FakeEmbedder(Embeddings):
    """Deterministic test embedder. Encodes strings into 4-dim vectors."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)

    @staticmethod
    def _encode(text: str) -> list[float]:
        # Stable, simple: length, vowel count, digit count, punctuation count.
        vowels = sum(1 for c in text.lower() if c in _VOWELS)
        digits = sum(1 for c in text if c.isdigit())
        punct = sum(1 for c in text if c in _PUNCTUATION)
        return [float(len(text)), float(vowels), float(digits), float(punct)]


# ---------- SQL contract ----------


class SQLConnectorContractTests:
    """Mixin: subclass must provide an async `connector` fixture."""

    @pytest.fixture
    async def connector(self) -> SQLConnector:
        raise NotImplementedError("Subclass must provide a 'connector' fixture")

    async def test_health_check_passes(self, connector: SQLConnector) -> None:
        health = await connector.health_check()
        assert health.ok is True

    async def test_dialect_is_string(self, connector: SQLConnector) -> None:
        assert isinstance(connector.dialect, str)
        assert len(connector.dialect) > 0

    async def test_upsert_and_query_table(self, connector: SQLConnector) -> None:
        df = pl.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
        await connector.upsert_table("contract_test_t", df, mode="replace")

        result = await connector.execute_query("SELECT id, name FROM contract_test_t ORDER BY id")
        assert result.columns == ["id", "name"]
        assert len(result.rows) == 3  # noqa: PLR2004
        assert result.rows[0] == (1, "a")

    async def test_list_tables_after_upsert(self, connector: SQLConnector) -> None:
        df = pl.DataFrame({"x": [1]})
        await connector.upsert_table("contract_listed", df, mode="replace")
        tables = await connector.list_tables()
        names = {t.name for t in tables}
        assert "contract_listed" in names

    async def test_list_tables_emits_column_statistics(self, connector: SQLConnector) -> None:
        """Spec §4: connectors that can compute cheap stats SHOULD do so.
        SQLite/Memory/Postgres all compute via polars. Failing this test on
        a new connector is a signal — either implement stats, or document
        the deferral with a None for column_statistics (still legal)."""
        df = pl.DataFrame(
            {
                "uf": ["MT", "PR", "SP", "MT", "MG"],
                "tons": [100, 80, 50, 60, 90],
            }
        )
        await connector.upsert_table("stats_t", df, mode="replace")
        tables = await connector.list_tables()
        info = next(t for t in tables if t.name == "stats_t")
        # column_statistics may be None for connectors that opted out, but
        # the SQL connectors HARA ships all populate it.
        assert info.column_statistics is not None
        stats = info.column_statistics
        expected_min = 50.0
        expected_max = 100.0
        expected_distinct = 4
        # Numeric column: min/max/mean populated
        assert "tons" in stats
        assert stats["tons"].min == expected_min
        assert stats["tons"].max == expected_max
        assert stats["tons"].mean is not None
        # Text column: distinct_count + top_values populated
        assert "uf" in stats
        assert stats["uf"].distinct_count == expected_distinct
        assert stats["uf"].top_values is not None
        assert "MT" in stats["uf"].top_values

    async def test_read_only_blocks_writes(self, connector: SQLConnector) -> None:
        # Attempting to write under read_only=True must fail.
        df = pl.DataFrame({"x": [1]})
        await connector.upsert_table("ro_test", df, mode="replace")
        with pytest.raises(Exception):  # noqa: B017, PT011 — connector-specific
            await connector.execute_query("UPDATE ro_test SET x = 2", read_only=True)


# ---------- Vector contract ----------


class VectorConnectorContractTests:
    """Mixin: subclass must provide an async `connector` fixture."""

    @pytest.fixture
    async def connector(self) -> VectorConnector:
        raise NotImplementedError("Subclass must provide a 'connector' fixture")

    @pytest.fixture
    def fake_embedder(self) -> Embeddings:
        return FakeEmbedder()

    @staticmethod
    def _make_chunks(file_id: str, n: int = 3) -> list[TextChunk]:
        return [
            TextChunk(file_id=file_id, content=f"chunk {i}", metadata={"page": i}) for i in range(n)
        ]

    async def test_health_check_passes(self, connector: VectorConnector) -> None:
        health = await connector.health_check()
        assert health.ok is True

    async def test_upsert_and_similarity_search(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_a", 5))
        results = await connector.similarity_search("chunk", k=3)
        assert len(results) <= 3  # noqa: PLR2004
        assert all(isinstance(r, TextChunk) for r in results)

    async def test_filter_by_file_id(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_b", 3))
        await connector.upsert_chunks(self._make_chunks("doc_c", 3))
        results = await connector.similarity_search("chunk", k=10, filter={"file_id": "doc_b"})
        assert all(r.file_id == "doc_b" for r in results)

    async def test_get_chunks_with_embeddings(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_d", 2))
        chunks = await connector.get_chunks("doc_d", include_embeddings=True)
        assert len(chunks) == 2  # noqa: PLR2004
        assert all(c.embedding is not None for c in chunks)
        assert all(len(c.embedding) > 0 for c in chunks if c.embedding)

    async def test_get_chunks_without_embeddings(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_e", 2))
        chunks = await connector.get_chunks("doc_e", include_embeddings=False)
        assert len(chunks) == 2  # noqa: PLR2004

    async def test_similarity_search_by_vector(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_f", 4))
        chunks = await connector.get_chunks("doc_f", include_embeddings=True)
        target_vec = chunks[0].embedding
        assert target_vec is not None
        results = await connector.similarity_search_by_vector(
            target_vec, k=2, filter={"file_id": "doc_f"}
        )
        assert len(results) <= 2  # noqa: PLR2004

    async def test_delete_document(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_g", 3))
        await connector.delete_document("doc_g")
        chunks = await connector.get_chunks("doc_g")
        assert chunks == []

    async def test_list_documents(self, connector: VectorConnector) -> None:
        await connector.upsert_chunks(self._make_chunks("doc_h", 2))
        docs = await connector.list_documents()
        names = {d.file_id for d in docs}
        assert "doc_h" in names
