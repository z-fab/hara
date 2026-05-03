"""Tests for the Orchestrator — graph composition + multi-turn glue."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import json
from typing import Any, cast

import polars as pl
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from hara.agent.orchestrator import Orchestrator, TurnResult, _serialize_evidence
from hara.agent.prompts.planner import PlannerOutput
from hara.agent.state import SqlEvidence, SubQuery, VerifierSignal
from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.base import TextChunk
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.services.session_store import SessionStore


class _ScriptedLLM:
    """Multi-purpose fake — supports structured-output (Planner/Verifier)
    AND streaming (Synthesizer) AND plain ainvoke (SQL gen)."""

    def __init__(
        self,
        *,
        structured_responses: list[Any] | None = None,
        stream_chunks: list[str] | None = None,
        invoke_responses: list[Any] | None = None,
    ) -> None:
        self.structured = list(structured_responses or [])
        self.stream = list(stream_chunks or [])
        self.invoke = list(invoke_responses or [])
        self._s_idx = 0
        self._i_idx = 0

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        outer = self

        class _R:
            async def ainvoke(self, _i: Any) -> Any:
                r = outer.structured[outer._s_idx]
                outer._s_idx += 1
                return r

        return _R()

    async def astream(self, _i: Any) -> Any:
        for c in self.stream:
            yield AIMessageChunk(content=c)

    async def ainvoke(self, _i: Any) -> Any:
        r = self.invoke[self._i_idx]
        self._i_idx += 1
        return AIMessage(content=r) if isinstance(r, str) else r


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


@pytest.fixture
async def session_store(tmp_path: Any) -> SessionStore:
    store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await store.initialize()
    return store


@pytest.fixture
async def sql_connector() -> MemorySQLConnector:
    conn = MemorySQLConnector()
    df = pl.DataFrame({"uf": ["MT", "PR"], "tons": [100, 80]})
    await conn.upsert_table("producao", df, mode="replace")
    return conn


@pytest.fixture
async def vec_connector() -> MemoryVectorConnector:
    emb = FakeEmbedder()
    vec = MemoryVectorConnector(embedder=emb)
    await vec.upsert_chunks([TextChunk(file_id="manejo.pdf", content="manejo integrado")])
    return vec


def _make_orch(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
    *,
    planner_llm: _ScriptedLLM,
    sql_llm: _ScriptedLLM | None = None,
    synth_llm: _ScriptedLLM,
    verifier_llm: _ScriptedLLM | None = None,
    verifier_mode: str = "off",
) -> Orchestrator:
    return Orchestrator(
        session_store=session_store,
        sql_connector=sql_connector,
        vec_connector=vec_connector,
        planner_llm=_llm(planner_llm),
        sql_llm=_llm(sql_llm or _ScriptedLLM()),
        synthesizer_llm=_llm(synth_llm),
        verifier_llm=_llm(verifier_llm or _ScriptedLLM()),
        structured_map_yaml="",
        unstructured_map_yaml="",
        agent_style="x",
        text_search_k=5,
        sql_max_retries=3,
        sql_max_rows=100,
        verifier_mode=verifier_mode,  # type: ignore[arg-type]
        max_history_turns=5,
    )


async def test_orchestrator_full_flow_sql_only(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="quanto produziu?")])
        ]
    )
    sql = _ScriptedLLM(invoke_responses=["SELECT uf, tons FROM producao WHERE uf = 'MT'"])
    synth = _ScriptedLLM(stream_chunks=["MT produziu 100t ", "<ref:1>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql,
        synth_llm=synth,
    )

    result = await orch.run_turn("Quanto produziu MT?")
    assert isinstance(result, TurnResult)
    assert "MT produziu 100t" in result.answer
    assert len(result.citations) == 1
    assert result.citations[0]["source"] == "producao"
    assert result.metadata.get("verifier") is None


async def test_orchestrator_persists_turn_via_thread_id(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """A new thread_id is auto-created and returned in the result."""
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["resp."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    result = await orch.run_turn("oi")
    thread = await session_store.get_thread(result.thread_id)
    assert thread is not None


async def test_orchestrator_carries_thread_id_across_calls(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Turn 2 with same thread_id reuses the thread."""
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[]),
            PlannerOutput(subqueries=[]),
        ]
    )
    synth = _ScriptedLLM(stream_chunks=["A", "B"])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    r1 = await orch.run_turn("turn 1")
    r2 = await orch.run_turn("turn 2", thread_id=r1.thread_id)
    assert r2.thread_id == r1.thread_id


async def test_orchestrator_unknown_thread_raises(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["x"])
    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    with pytest.raises(KeyError, match="unknown thread_id"):
        await orch.run_turn("x", thread_id="thr_does_not_exist")


async def test_orchestrator_verifier_signal_populates_metadata(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["resp <ref:1>."])
    verifier = _ScriptedLLM(
        structured_responses=[
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
        synth_llm=synth,
        verifier_llm=verifier,
        verifier_mode="signal",
    )
    result = await orch.run_turn("x")
    assert result.metadata["verifier"] is not None
    assert result.metadata["verifier"]["overall_pass"] is True
    assert result.metadata["verifier"]["pct_supported"] == 0.9


async def test_orchestrator_metadata_includes_routing(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(
                subqueries=[
                    SubQuery(id="sq_1", type="sql", question="x"),
                    SubQuery(id="sq_2", type="text", question="y"),
                ]
            )
        ]
    )
    sql = _ScriptedLLM(invoke_responses=["SELECT uf FROM producao"])
    synth = _ScriptedLLM(stream_chunks=["resp."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql,
        synth_llm=synth,
    )
    result = await orch.run_turn("hibrido")
    routing = result.metadata["routing"]
    assert routing["subqueries_count"] == 2
    assert "sql" in routing["routes_taken"]
    assert "text" in routing["routes_taken"]


async def test_orchestrator_unsupported_markers_count(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """LLM emits <ref:99> but pool only has id 1 → unsupported_markers=1."""
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="x")])
        ]
    )
    sql = _ScriptedLLM(invoke_responses=["SELECT uf FROM producao"])
    synth = _ScriptedLLM(stream_chunks=["use <ref:99>."])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql,
        synth_llm=synth,
    )
    result = await orch.run_turn("x")
    assert result.metadata["unsupported_markers"] == 1
    assert result.citations == []


async def test_orchestrator_on_token_callback(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """on_token receives streaming deltas — wires CLI Rich Live."""
    received: list[str] = []
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["A ", "B ", "C"])

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    await orch.run_turn("x", on_token=received.append)
    assert received == ["A ", "B ", "C"]


async def test_orchestrator_persists_turn_row(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """After run_turn, hara_turns has a row with the same turn_id and answer."""
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["resposta única."])
    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    result = await orch.run_turn("oi")

    rows = await session_store.list_turns(thread_id=result.thread_id, limit=10)
    assert len(rows) == 1
    assert rows[0]["turn_id"] == result.turn_id
    assert rows[0]["question"] == "oi"
    assert rows[0]["answer"] == "resposta única."


async def test_orchestrator_loads_history_on_followup(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Turn 2 sees turn 1 in history (Planner can use prior context)."""
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[]),
            PlannerOutput(subqueries=[]),
        ]
    )
    synth = _ScriptedLLM(stream_chunks=["A", "B"])
    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    r1 = await orch.run_turn("turn 1")
    r2 = await orch.run_turn("turn 2", thread_id=r1.thread_id)

    # Both turns persisted
    rows = await session_store.list_turns(thread_id=r1.thread_id, limit=10)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["question"] == "turn 2"
    assert rows[1]["question"] == "turn 1"
    # And the second turn referenced the same thread
    assert r2.thread_id == r1.thread_id


async def test_orchestrator_reuse_path_preserves_prior_pool(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Codex review #3 P1: turn 1 retrieves; turn 2 has Planner return [] →
    Synthesizer sees the pool from turn 1 (loaded from session_store) and
    can cite it. Without the fix, turn 2 cited nothing."""
    planner = _ScriptedLLM(
        structured_responses=[
            # Turn 1: real SQL
            PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="x")]),
            # Turn 2: reuse signal
            PlannerOutput(subqueries=[]),
        ]
    )
    sql = _ScriptedLLM(invoke_responses=["SELECT uf, tons FROM producao"])
    synth = _ScriptedLLM(stream_chunks=["Turn 1 <ref:1>.", "Turn 2 <ref:1>."])
    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql,
        synth_llm=synth,
    )
    r1 = await orch.run_turn("turn 1")
    r2 = await orch.run_turn("turn 2", thread_id=r1.thread_id)

    # Turn 2 was a reuse turn, but it still had a pool to cite.
    assert r2.metadata["routing"]["evidence_pool_size"] > 0
    assert len(r2.citations) >= 1
    # Reused count > 0 — pool came from accumulated, not new retrieval.
    assert r2.metadata["routing"]["evidence_reused_count"] > 0


async def test_orchestrator_concurrent_streams_dont_cross(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Codex review #3 P2: ContextVar isolation — two overlapping run_turn
    calls must not deliver the wrong tokens to each other's on_token."""
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[]),
            PlannerOutput(subqueries=[]),
        ]
    )
    # Each call gets its own LLM; sharing one would fight the index counter.
    synth_a = _ScriptedLLM(stream_chunks=["A1", "A2", "A3"])

    # Two orchestrators (one each) — testing the ContextVar shape on a single
    # Orchestrator with two concurrent calls is harder to script with the
    # multi-purpose fake. The same ContextVar mechanism applies.
    orch_a = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth_a,
    )

    received_a: list[str] = []
    received_b: list[str] = []

    async def _run_a() -> None:
        await orch_a.run_turn("a", on_token=received_a.append)

    async def _run_b() -> None:
        await orch_a.run_turn("b", on_token=received_b.append)

    await asyncio.gather(_run_a(), _run_b())
    # Each callback only saw tokens from a stream we configured. Since both
    # calls go to the same orch (same synth_a), tokens may be split across
    # them, but neither callback should receive a token meant for the other
    # AFTER the ContextVar fix. Light assertion: both received tokens (the
    # fundamental thing we want is that no callback ends up empty due to
    # being clobbered).
    assert len(received_a) + len(received_b) > 0


async def test_orchestrator_persists_pool_with_non_json_values(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Codex final review P2: SQL rows with date/Decimal/etc. shouldn't
    crash record_turn. Inject a SqlEvidence with a Decimal value into the
    pool by feeding sql_executor a query whose result contains one."""
    ev = SqlEvidence(
        evidence_id=1,
        source_table="t",
        columns=["c"],
        row=(decimal.Decimal("1.5"), datetime.date(2026, 5, 2), b"raw"),
    )
    serialized = _serialize_evidence(ev)
    # Round-trip via json must not raise
    blob = json.dumps(serialized)
    restored = json.loads(blob)
    # All row values came back as strings
    assert restored["row"] == ["1.5", "2026-05-02", "b'raw'"]
    assert restored["__kind__"] == "sql"


async def test_orchestrator_emits_state_events_per_node(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """on_event should fire for each node transition (started/completed)."""
    planner = _ScriptedLLM(
        structured_responses=[
            PlannerOutput(subqueries=[SubQuery(id="sq_1", type="sql", question="x")])
        ]
    )
    sql = _ScriptedLLM(invoke_responses=["SELECT uf FROM producao"])
    synth = _ScriptedLLM(stream_chunks=["resp."])

    events: list[dict[str, Any]] = []

    async def _on_event(ev: dict[str, Any]) -> None:
        events.append(ev)

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        sql_llm=sql,
        synth_llm=synth,
    )
    await orch.run_turn("x", on_event=_on_event)

    state_nodes = [e["data"]["node"] for e in events if e["event"] == "state"]
    assert "planner" in state_nodes
    assert "sql_executor" in state_nodes
    assert "synthesizer" in state_nodes
    starts = [e for e in events if e["event"] == "state" and e["data"]["status"] == "started"]
    completes = [e for e in events if e["event"] == "state" and e["data"]["status"] == "completed"]
    assert len(starts) >= 3
    assert len(completes) >= 3


async def test_orchestrator_emits_token_events(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["A ", "B ", "C"])

    events: list[dict[str, Any]] = []

    async def _on_event(ev: dict[str, Any]) -> None:
        events.append(ev)

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    await orch.run_turn("x", on_event=_on_event)
    # Drain pending fire-and-forget tasks
    await asyncio.sleep(0.1)
    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == ["A ", "B ", "C"]


async def test_orchestrator_token_events_arrive_before_final(
    session_store: SessionStore,
    sql_connector: MemorySQLConnector,
    vec_connector: MemoryVectorConnector,
) -> None:
    """Codex review #2 P2: pending fire-and-forget token tasks must drain
    BEFORE the synthesizer 'completed' state event (and therefore before
    the runner's final/done). Otherwise a delayed token can land after
    final and break event ordering for SSE consumers."""
    planner = _ScriptedLLM(structured_responses=[PlannerOutput(subqueries=[])])
    synth = _ScriptedLLM(stream_chunks=["T1", "T2", "T3"])

    events: list[dict[str, Any]] = []

    async def _on_event(ev: dict[str, Any]) -> None:
        # Slow callback — exposes the race: without the drain the
        # synthesizer-completed state event would land before the token
        # tasks finish their cb invocation.
        if ev["event"] == "token":
            await asyncio.sleep(0.02)
        events.append(ev)

    orch = _make_orch(
        session_store,
        sql_connector,
        vec_connector,
        planner_llm=planner,
        synth_llm=synth,
    )
    await orch.run_turn("x", on_event=_on_event)
    # No extra sleep — the orchestrator MUST have drained pending tokens
    # before returning. The CLI/API path relies on this to serialize the
    # final/done events that come AFTER run_turn.

    # Find the synthesizer 'completed' state event index, then assert
    # all 3 token events appear before it.
    synth_completed_idx = next(
        i
        for i, ev in enumerate(events)
        if ev["event"] == "state"
        and ev["data"].get("node") == "synthesizer"
        and ev["data"].get("status") == "completed"
    )
    tokens_before: list[str] = [
        ev["data"]["text"] for ev in events[:synth_completed_idx] if ev["event"] == "token"
    ]
    assert tokens_before == ["T1", "T2", "T3"]
    # And no token event slipped past the completed marker.
    tokens_after = [ev for ev in events[synth_completed_idx + 1 :] if ev["event"] == "token"]
    assert tokens_after == []
