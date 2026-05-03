"""End-to-end agent test using FakeChatModel + memory connectors.

No external services. Validates the wiring: ingest data -> semantic_map
context -> orchestrator -> answer with citations + metadata. Spec section 17
critério 18 (`hara chat` matches /invoke for same input — implicit since
both go through the same Orchestrator).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from hara.agent.orchestrator import Orchestrator
from hara.agent.prompts.planner import PlannerOutput
from hara.agent.state import SubQuery, VerifierSignal
from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.base import TextChunk
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.services.session_store import SessionStore


class _MultiPurposeFakeLLM:
    """Implements with_structured_output, ainvoke, AND astream."""

    def __init__(
        self,
        *,
        structured: list[Any] | None = None,
        invoke: list[Any] | None = None,
        stream: list[str] | None = None,
    ) -> None:
        self._s = list(structured or [])
        self._i = list(invoke or [])
        self._stream = list(stream or [])
        self._sx = 0
        self._ix = 0

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        outer = self

        class _R:
            async def ainvoke(self, _i: Any) -> Any:
                r = outer._s[outer._sx]
                outer._sx += 1
                return r

        return _R()

    async def ainvoke(self, _i: Any) -> Any:
        r = self._i[self._ix]
        self._ix += 1
        return AIMessage(content=r) if isinstance(r, str) else r

    async def astream(self, _i: Any) -> Any:
        for c in self._stream:
            yield AIMessageChunk(content=c)


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


@pytest.fixture
async def session_store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await s.initialize()
    return s


@pytest.fixture
async def sql_connector() -> MemorySQLConnector:
    conn = MemorySQLConnector()
    df = pl.DataFrame(
        {
            "uf": ["MT", "PR", "SP", "MG"],
            "tons": [100, 80, 50, 60],
        }
    )
    await conn.upsert_table("producao", df, mode="replace")
    return conn


@pytest.fixture
async def vec_connector() -> MemoryVectorConnector:
    emb = FakeEmbedder()
    vec = MemoryVectorConnector(embedder=emb)
    await vec.upsert_chunks(
        [
            TextChunk(
                file_id="manejo.pdf",
                content="O manejo integrado de pragas combina técnicas.",
                metadata={"section": "1.1"},
            ),
        ]
    )
    return vec


def _make_orch(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
    *,
    planner_llm: _MultiPurposeFakeLLM,
    sql_llm: _MultiPurposeFakeLLM | None = None,
    synth_llm: _MultiPurposeFakeLLM,
    verifier_llm: _MultiPurposeFakeLLM | None = None,
    verifier_mode: str = "off",
    structured_yaml: str = "",
    unstructured_yaml: str = "",
) -> Orchestrator:
    return Orchestrator(
        session_store=session_store,
        sql_connector=sql_connector,
        vec_connector=vec_connector,
        planner_llm=_llm(planner_llm),
        sql_llm=_llm(sql_llm or _MultiPurposeFakeLLM()),
        synthesizer_llm=_llm(synth_llm),
        verifier_llm=_llm(verifier_llm or _MultiPurposeFakeLLM()),
        structured_map_yaml=structured_yaml,
        unstructured_map_yaml=unstructured_yaml,
        agent_style="x",
        text_search_k=5,
        sql_max_retries=3,
        sql_max_rows=100,
        verifier_mode=verifier_mode,  # type: ignore[arg-type]
        max_history_turns=5,
    )


async def test_e2e_sql_only_happy_path(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Pergunta SQL-only: planner -> sql_executor -> synthesizer -> resposta."""
    planner = _MultiPurposeFakeLLM(
        structured=[
            PlannerOutput(
                subqueries=[SubQuery(id="sq_1", type="sql", question="quanto MT produziu?")]
            )
        ]
    )
    sql_llm = _MultiPurposeFakeLLM(invoke=["SELECT uf, tons FROM producao WHERE uf = 'MT'"])
    synth = _MultiPurposeFakeLLM(stream=["MT produziu ", "100 toneladas ", "<ref:1>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql_llm,
        synth_llm=synth,
        structured_yaml=(
            "tables:\n  - table_name: producao\n"
            "    description: Produção de soja por UF\n"
            "    columns: [uf, tons]\n"
        ),
    )

    result = await orch.run_turn("Quanto produziu MT?")

    assert "MT produziu" in result.answer
    assert "<ref:1>" in result.answer
    assert len(result.citations) == 1
    assert result.citations[0]["source"] == "producao"
    assert result.citations[0]["kind"] == "sql"
    assert result.metadata["routing"]["routes_taken"] == ["sql"]
    assert result.metadata["unsupported_markers"] == 0


async def test_e2e_text_only_happy_path(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _MultiPurposeFakeLLM(
        structured=[
            PlannerOutput(
                subqueries=[SubQuery(id="sq_1", type="text", question="manejo de pragas?")]
            )
        ]
    )
    synth = _MultiPurposeFakeLLM(stream=["O manejo integrado ", "combina técnicas <ref:1>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
        unstructured_yaml="documents:\n  - file_id: manejo.pdf\n",
    )

    result = await orch.run_turn("O que é manejo integrado?")

    assert "manejo integrado" in result.answer
    assert len(result.citations) == 1
    assert result.citations[0]["kind"] == "text"
    assert result.citations[0]["source"] == "manejo.pdf"
    assert result.metadata["routing"]["routes_taken"] == ["text"]


async def test_e2e_hybrid_sql_and_text(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _MultiPurposeFakeLLM(
        structured=[
            PlannerOutput(
                subqueries=[
                    SubQuery(id="sq_1", type="sql", question="x"),
                    SubQuery(id="sq_2", type="text", question="y"),
                ]
            )
        ]
    )
    sql_llm = _MultiPurposeFakeLLM(invoke=["SELECT uf FROM producao"])
    synth = _MultiPurposeFakeLLM(stream=["MT produz <ref:1>; ", "manejo combina <ref:5>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql_llm,
        synth_llm=synth,
        structured_yaml="tables:\n  - table_name: producao\n",
        unstructured_yaml="documents:\n  - file_id: manejo.pdf\n",
    )

    result = await orch.run_turn("MT produz e o que é manejo?")
    routes = result.metadata["routing"]["routes_taken"]
    assert "sql" in routes
    assert "text" in routes
    # 4 SQL evidences (rows) + 1 text evidence = 5 total. Text is id=5.
    # The evidence_pool is sql-first then text in ID order.
    pool_size = result.metadata["routing"]["evidence_pool_size"]
    assert pool_size == 5
    # Synthesizer cited <ref:1> (sql) and <ref:5> (text)
    cite_ids = sorted(c["evidence_id"] for c in result.citations)
    assert cite_ids == [1, 5]


async def test_e2e_multi_turn_reuse(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Turn 1: real retrieval (planner emits SQL subquery, sql_executor runs).
    Turn 2: planner returns [] -> reuse path. Synthesizer should still cite
    using the pool persisted from turn 1 (via metadata._evidence_pool).
    Validates spec section 17 criterio 12 (multi-turno reuse observable)."""
    planner = _MultiPurposeFakeLLM(
        structured=[
            PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="MT?")]),
            PlannerOutput(subqueries=[]),  # turn 2: reuse
        ]
    )
    sql_llm = _MultiPurposeFakeLLM(invoke=["SELECT uf, tons FROM producao WHERE uf = 'MT'"])
    synth = _MultiPurposeFakeLLM(stream=["MT produziu 100t <ref:1>.", "Ainda produz <ref:1>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql_llm,
        synth_llm=synth,
        structured_yaml="tables:\n  - table_name: producao\n",
    )
    r1 = await orch.run_turn("Quanto produziu MT?")
    r2 = await orch.run_turn("Continua produzindo?", thread_id=r1.thread_id)

    assert r2.thread_id == r1.thread_id
    # Turn 2 was reuse - no new subqueries emitted
    assert r2.metadata["routing"]["subqueries_count"] == 0
    assert r2.metadata["routing"]["routes_taken"] == []
    # But the pool was preserved via _evidence_pool persistence
    assert r2.metadata["routing"]["evidence_pool_size"] > 0
    assert r2.metadata["routing"]["evidence_reused_count"] > 0
    # And the answer cites that pool
    assert len(r2.citations) >= 1


async def test_e2e_verifier_signal(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """[verifier].mode = signal -> metadata.verifier populated with the four
    spec fields; verifier doesn't block the response (answer unchanged)."""
    planner = _MultiPurposeFakeLLM(
        structured=[PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="x")])]
    )
    sql_llm = _MultiPurposeFakeLLM(invoke=["SELECT uf FROM producao"])
    synth = _MultiPurposeFakeLLM(stream=["MT <ref:1>."])
    verifier_llm = _MultiPurposeFakeLLM(
        structured=[
            VerifierSignal(
                overall_pass=True,
                pct_supported=0.9,
                n_missing_aspects=0,
                weak_sentences=[],
            )
        ]
    )

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql_llm,
        synth_llm=synth,
        verifier_llm=verifier_llm,
        verifier_mode="signal",
        structured_yaml="tables:\n  - table_name: producao\n",
    )

    result = await orch.run_turn("x")
    assert result.metadata["verifier"] is not None
    assert result.metadata["verifier"]["overall_pass"] is True
    assert result.metadata["verifier"]["pct_supported"] == 0.9
    assert result.metadata["verifier"]["n_missing_aspects"] == 0
    # Answer is unchanged regardless of verifier outcome
    assert "MT" in result.answer
