"""Contract tests for ChromaDBConnector. Requires `chromadb` extra."""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings

from hara.config.schemas import ChromaDBVectorConfig
from hara.connectors.testing import FakeEmbedder, VectorConnectorContractTests
from hara.connectors.vector.base import TextChunk
from hara.connectors.vector.chromadb import ChromaDBConnector


@pytest.mark.contract
class TestChromaDBConnector(VectorConnectorContractTests):
    @pytest.fixture
    async def connector(self, fake_embedder: Embeddings, tmp_path) -> ChromaDBConnector:
        cfg = ChromaDBVectorConfig(type="chromadb", persist_directory=tmp_path)
        return ChromaDBConnector.from_config(cfg, embedder=fake_embedder)


@pytest.mark.contract
async def test_chunk_ids_stable_across_connector_instances(tmp_path) -> None:
    """Ingesting the same content into a fresh connector must reuse IDs.

    Regression: Python's hash() is salted per process, so the previous
    `abs(hash(...))`-based ID would change between runs and Chroma would
    accumulate duplicate chunks instead of replacing them.
    """
    cfg = ChromaDBVectorConfig(type="chromadb", persist_directory=tmp_path)

    # First run.
    conn1 = ChromaDBConnector.from_config(cfg, embedder=FakeEmbedder())
    await conn1.upsert_chunks(
        [TextChunk(file_id="doc_stable", content="repeatable text", metadata={})]
    )
    docs1 = await conn1.list_documents()
    assert any(d.file_id == "doc_stable" and d.chunk_count == 1 for d in docs1)

    # Second run on the SAME persist_directory with the same content. If IDs are
    # stable, upsert is a no-op (count stays 1). If IDs were random per-process,
    # we would now have 2 chunks under "doc_stable".
    conn2 = ChromaDBConnector.from_config(cfg, embedder=FakeEmbedder())
    await conn2.upsert_chunks(
        [TextChunk(file_id="doc_stable", content="repeatable text", metadata={})]
    )
    docs2 = await conn2.list_documents()
    matching = [d for d in docs2 if d.file_id == "doc_stable"]
    assert len(matching) == 1
    assert matching[0].chunk_count == 1, (
        f"Expected 1 chunk after re-ingest, got {matching[0].chunk_count}"
    )


@pytest.mark.contract
async def test_chunk_ids_distinguish_repeated_content_by_index(tmp_path) -> None:
    """Two chunks with identical content but different ``chunk_index`` must
    survive ``upsert`` as two separate rows.

    Regression: previously the chunk ID was ``f"{file_id}_{content_hash}"``,
    so repeated boilerplate (or chunker overlap) collapsed two distinct
    logical chunks into one Chroma row, while ``session_store`` recorded
    ``chunks_count=2``. Counts diverged silently. The fix incorporates
    ``metadata.chunk_index`` into the ID.
    """
    cfg = ChromaDBVectorConfig(type="chromadb", persist_directory=tmp_path)
    conn = ChromaDBConnector.from_config(cfg, embedder=FakeEmbedder())

    await conn.upsert_chunks(
        [
            TextChunk(
                file_id="doc_dup",
                content="boilerplate header",
                metadata={"chunk_index": 0},
            ),
            TextChunk(
                file_id="doc_dup",
                content="boilerplate header",
                metadata={"chunk_index": 1},
            ),
        ]
    )
    docs = await conn.list_documents()
    matching = [d for d in docs if d.file_id == "doc_dup"]
    assert len(matching) == 1
    assert matching[0].chunk_count == 2, (
        f"Expected 2 chunks (distinct chunk_index), got {matching[0].chunk_count}"
    )
