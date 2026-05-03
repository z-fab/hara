"""Tests for the Text Retriever node."""

from __future__ import annotations

from hara.agent.nodes.text_retriever import text_retriever_node
from hara.agent.state import AgentState, SubQuery
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.base import TextChunk
from hara.connectors.vector.memory import MemoryVectorConnector


def _state(subqueries: list[SubQuery]) -> AgentState:
    return {
        "question": "x",
        "thread_id": "t",
        "turn_id": "tr",
        "history": [],
        "accumulated_evidence": [],
        "subqueries": subqueries,
        "routes_taken": {sq.type for sq in subqueries},
    }


async def _make_vec_connector() -> MemoryVectorConnector:
    emb = FakeEmbedder()
    vec = MemoryVectorConnector(embedder=emb)
    await vec.upsert_chunks(
        [
            TextChunk(file_id="manejo.pdf", content="manejo integrado de pragas"),
            TextChunk(file_id="manejo.pdf", content="rotação de culturas"),
            TextChunk(file_id="outro.pdf", content="produtividade do solo"),
        ]
    )
    return vec


async def test_text_retriever_runs_each_subquery() -> None:
    vec = await _make_vec_connector()
    state = _state(
        [
            SubQuery(id="sq_1", type="text", question="manejo"),
            SubQuery(id="sq_2", type="text", question="solo"),
        ]
    )
    delta = await text_retriever_node(state, connector=vec, k=2)
    assert "text_results" in delta
    assert len(delta["text_results"]) == 2


async def test_text_retriever_skips_sql_subqueries() -> None:
    vec = await _make_vec_connector()
    state = _state(
        [
            SubQuery(id="sq_1", type="sql", question="x"),
            SubQuery(id="sq_2", type="text", question="manejo"),
        ]
    )
    delta = await text_retriever_node(state, connector=vec, k=2)
    assert len(delta["text_results"]) == 1


async def test_text_retriever_respects_k() -> None:
    vec = await _make_vec_connector()
    state = _state([SubQuery(id="sq_1", type="text", question="manejo")])
    delta = await text_retriever_node(state, connector=vec, k=1)
    chunks = delta["text_results"][0]["chunks"]
    assert len(chunks) <= 1


async def test_text_retriever_chunk_shape() -> None:
    """Each chunk dict has file_id, content, metadata — shape evidence_pool expects."""
    vec = await _make_vec_connector()
    state = _state([SubQuery(id="sq_1", type="text", question="manejo")])
    delta = await text_retriever_node(state, connector=vec, k=2)
    chunk = delta["text_results"][0]["chunks"][0]
    assert "file_id" in chunk
    assert "content" in chunk
    assert "metadata" in chunk


async def test_text_retriever_filters_by_sources() -> None:
    """When subquery sources is set, filter is applied — the memory connector
    now honors the Chroma-style ``{"file_id": {"$in": [...]}}`` shape that
    the node emits, so this assertion is non-vacuous: it confirms `outro.pdf`
    chunks are excluded.
    """
    vec = await _make_vec_connector()
    state = _state([SubQuery(id="sq_1", type="text", question="x", sources=["manejo.pdf"])])
    delta = await text_retriever_node(state, connector=vec, k=10)
    chunks = delta["text_results"][0]["chunks"]
    assert len(chunks) > 0  # filter must not silently swallow everything
    assert all(c["file_id"] == "manejo.pdf" for c in chunks)


async def test_text_retriever_records_tracking() -> None:
    vec = await _make_vec_connector()
    state = _state([SubQuery(id="sq_1", type="text", question="x")])
    delta = await text_retriever_node(state, connector=vec, k=2)
    assert "latency_per_node" in delta
    assert "text_retriever" in delta["latency_per_node"]


async def test_text_retriever_empty_subqueries() -> None:
    vec = await _make_vec_connector()
    state = _state([])
    delta = await text_retriever_node(state, connector=vec, k=2)
    assert delta["text_results"] == []
