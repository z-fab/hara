"""Top-level orchestrator: builds the graph, runs a turn, packages the response.

Used by `hara chat` (CLI) directly. Plano 4 will use the same orchestrator
under the FastAPI handlers — we keep it transport-agnostic.

`_load_history` reads from `SessionStore.list_turns`; `_load_accumulated`
reads back the most recent turn's evidence pool via the same store
(persisted under `metadata["_evidence_pool"]` by `_package_result`).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Literal, cast

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from hara.agent.graph import GraphConfig, build_graph
from hara.agent.markers import build_citations, count_unsupported_markers
from hara.agent.nodes.planner import planner_node
from hara.agent.nodes.sql_executor import sql_executor_node
from hara.agent.nodes.synthesizer import synthesizer_node
from hara.agent.nodes.text_retriever import text_retriever_node
from hara.agent.nodes.verifier import verifier_node
from hara.agent.state import (
    AgentState,
    Evidence,
    Message,
    SqlEvidence,
    TextEvidence,
    TokenUsage,
    VerifierSignal,
)
from hara.connectors.sql.base import SQLConnector
from hara.connectors.vector.base import VectorConnector
from hara.services.session_store import SessionStore

log = logging.getLogger(__name__)

# Per-turn streaming callback. ContextVar (not an instance attribute) so
# concurrent ``run_turn`` calls — Plano 4's API will have them — don't
# clobber each other's callback. Each asyncio task gets its own copy
# automatically; the synthesizer closure reads via ``.get(None)``.
_ON_TOKEN: ContextVar[Callable[[str], None] | None] = ContextVar(
    "_hara_orch_on_token", default=None
)

# Per-turn structured event callback. Async (the API persists each event
# to SQLite + publishes on EventBus before returning), so the orchestrator
# awaits the callback for state events. Token events are emitted
# fire-and-forget from the synthesizer to avoid blocking streaming.
_ON_EVENT: ContextVar[Callable[[dict[str, Any]], Awaitable[None]] | None] = ContextVar(
    "_hara_orch_on_event", default=None
)


async def _emit_state(node: str, status: str, **extras: Any) -> None:
    """Emit a ``state`` event for the current turn, if a callback is bound."""
    cb = _ON_EVENT.get()
    if cb is None:
        return
    payload: dict[str, Any] = {
        "event": "state",
        "data": {"node": node, "status": status, **extras},
    }
    await cb(payload)


class TurnResult(BaseModel):
    """Final outcome of a turn — what `hara chat` renders, what /invoke returns."""

    thread_id: str
    turn_id: str
    answer: str
    citations: list[dict[str, Any]]
    metadata: dict[str, Any]


class Orchestrator:
    """Compose graph + manage turns. Sequential calls share state cleanly;
    concurrent calls (Plano 4 API) get fresh AgentState each time.
    """

    def __init__(  # noqa: PLR0915 - five inlined node closures with state-event wrappers; splitting would require carrying the on_event closures plus the LLM/connector deps through helpers and would obscure the graph wiring.
        self,
        *,
        session_store: SessionStore,
        sql_connector: SQLConnector,
        vec_connector: VectorConnector,
        planner_llm: BaseChatModel,
        sql_llm: BaseChatModel,
        synthesizer_llm: BaseChatModel,
        verifier_llm: BaseChatModel,
        structured_map_yaml: str,
        unstructured_map_yaml: str,
        agent_style: str,
        text_search_k: int,
        sql_max_retries: int,
        sql_max_rows: int,
        verifier_mode: Literal["off", "signal"],
        max_history_turns: int,
    ) -> None:
        self._store = session_store
        self._verifier_mode = verifier_mode
        self._max_history_turns = max_history_turns

        async def _planner(state: AgentState) -> dict[str, Any]:
            await _emit_state("planner", "started")
            try:
                result = await planner_node(
                    state,
                    llm=planner_llm,
                    structured_map_yaml=structured_map_yaml,
                    unstructured_map_yaml=unstructured_map_yaml,
                )
            except Exception:
                await _emit_state("planner", "failed")
                raise
            extras: dict[str, Any] = {}
            subs = result.get("subqueries")
            if subs is not None:
                extras["subqueries_count"] = len(subs)
            await _emit_state("planner", "completed", **extras)
            return result

        async def _sql_exec(state: AgentState) -> dict[str, Any]:
            await _emit_state("sql_executor", "started")
            try:
                result = await sql_executor_node(
                    state,
                    llm=sql_llm,
                    connector=sql_connector,
                    structured_map_yaml=structured_map_yaml,
                    max_retries=sql_max_retries,
                    max_rows=sql_max_rows,
                )
            except Exception:
                await _emit_state("sql_executor", "failed")
                raise
            extras: dict[str, Any] = {}
            sql_results = result.get("sql_results")
            if isinstance(sql_results, list):
                rows_count = 0
                for entry in cast(list[Any], sql_results):
                    if isinstance(entry, dict):
                        rows = cast(dict[str, Any], entry).get("rows")
                        if isinstance(rows, list):
                            rows_count += len(cast(list[Any], rows))
                extras["rows_count"] = rows_count
            await _emit_state("sql_executor", "completed", **extras)
            return result

        async def _text_ret(state: AgentState) -> dict[str, Any]:
            await _emit_state("text_retriever", "started")
            try:
                result = await text_retriever_node(state, connector=vec_connector, k=text_search_k)
            except Exception:
                await _emit_state("text_retriever", "failed")
                raise
            extras: dict[str, Any] = {}
            text_results = result.get("text_results")
            if isinstance(text_results, list):
                chunks_count = 0
                for entry in cast(list[Any], text_results):
                    if isinstance(entry, dict):
                        chunks = cast(dict[str, Any], entry).get("chunks")
                        if isinstance(chunks, list):
                            chunks_count += len(cast(list[Any], chunks))
                extras["chunks_count"] = chunks_count
            await _emit_state("text_retriever", "completed", **extras)
            return result

        async def _synth(state: AgentState) -> dict[str, Any]:
            await _emit_state("synthesizer", "started")
            current_on_token = _ON_TOKEN.get()
            on_event_cb = _ON_EVENT.get()
            # Track per-call fire-and-forget token tasks so we can drain
            # them BEFORE emitting ``synthesizer`` ``completed`` (and
            # ultimately ``final``/``done``). Otherwise a delayed token
            # task could land after ``final`` and break event ordering.
            pending_tokens: list[asyncio.Task[None]] = []

            def _combined_token(text: str) -> None:
                if current_on_token is not None:
                    current_on_token(text)
                if on_event_cb is not None:
                    # Fire-and-forget — don't block streaming on DB INSERT.
                    # Wrap in a coroutine since ``on_event_cb`` is typed as
                    # ``Awaitable[None]`` (returning a coroutine is the
                    # common case but the protocol allows any awaitable;
                    # ``asyncio.create_task`` requires a coroutine).
                    cb = on_event_cb

                    async def _emit_token(t: str = text) -> None:
                        await cb({"event": "token", "data": {"text": t}})

                    pending_tokens.append(asyncio.create_task(_emit_token()))

            try:
                result = await synthesizer_node(
                    state,
                    llm=synthesizer_llm,
                    style=agent_style,
                    on_token=_combined_token,
                )
            except Exception:
                # Still drain so callbacks observing the bus don't deadlock
                # on a never-completing pending task.
                if pending_tokens:
                    await asyncio.gather(*pending_tokens, return_exceptions=True)
                await _emit_state("synthesizer", "failed")
                raise
            # Drain pending token emits BEFORE the completed state event so
            # consumers always see tokens → completed → final → done.
            if pending_tokens:
                await asyncio.gather(*pending_tokens, return_exceptions=True)
            await _emit_state("synthesizer", "completed")
            return result

        async def _verifier(state: AgentState) -> dict[str, Any]:
            await _emit_state("verifier", "started")
            try:
                result = await verifier_node(state, llm=verifier_llm)
            except Exception:
                await _emit_state("verifier", "failed")
                raise
            extras: dict[str, Any] = {}
            sig = result.get("verifier_signal")
            if isinstance(sig, VerifierSignal):
                extras["overall_pass"] = sig.overall_pass
            await _emit_state("verifier", "completed", **extras)
            return result

        cfg = GraphConfig(verifier_mode=verifier_mode)
        self._graph = build_graph(
            cfg,
            planner=_planner,
            sql_executor=_sql_exec,
            text_retriever=_text_ret,
            synthesizer=_synth,
            verifier=_verifier,
        )

    async def run_turn(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Callable[[str], None] | None = None,
        on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> TurnResult:
        """Execute one turn. Creates/uses thread; emits TurnResult.

        ``turn_id`` is optional. When ``None`` (CLI path), the orchestrator
        generates one and persists the row at the end via
        :meth:`SessionStore.record_turn`. When provided (API path), the
        caller has already pre-created the row (``status='running'``) so
        events can FK to it during the run; we skip the internal
        ``record_turn`` and instead finalize the existing row via
        :meth:`SessionStore.update_turn_result`. Without this distinction
        the API would end up with a duplicate row and the original
        ``running`` row would never reach a terminal status.
        """
        if thread_id is None:
            thread_id = await self._store.create_thread()
            history: list[Message] = []
            accumulated: list[Evidence] = []
        else:
            existing = await self._store.get_thread(thread_id)
            if existing is None:
                raise KeyError(f"unknown thread_id {thread_id!r}")
            history = await self._load_history(thread_id)
            accumulated = await self._load_accumulated(thread_id)

        # Caller may pre-create the row (API); fall back to a fresh id (CLI).
        caller_owns_row = turn_id is not None
        if turn_id is None:
            turn_id = f"trn_{secrets.token_urlsafe(12)}"

        initial: AgentState = {
            "question": question,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "history": history,
            "accumulated_evidence": accumulated,
            "subqueries": [],
            "routes_taken": set(),
            "sql_results": [],
            "text_results": [],
            "answer_text": "",
            "evidence_pool": [],
            "verifier_signal": None,
            "tokens": TokenUsage(),
            "latency_per_node": {},
        }

        # ``cv_token`` / ``cv_event`` are ContextVar Tokens (used to reset
        # the vars), NOT LLM tokens. Naming kept distinct to avoid confusion
        # with streaming token callbacks above.
        cv_token = _ON_TOKEN.set(on_token)
        cv_event = _ON_EVENT.set(on_event)
        try:
            final_state: AgentState = await self._graph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAssignmentType]
        finally:
            _ON_TOKEN.reset(cv_token)
            _ON_EVENT.reset(cv_event)

        result = self._package_result(final_state, thread_id, turn_id)
        if caller_owns_row:
            # API path: the row already exists with status='running'. Update
            # it in place to status='completed' and persist the final
            # answer/citations/metadata. Avoids the duplicate row that would
            # otherwise be inserted under a different (orchestrator-internal)
            # turn_id, leaving the API's pre-created row stuck on 'running'.
            await self._store.update_turn_result(
                turn_id=turn_id,
                answer=result.answer,
                citations=result.citations,
                metadata=result.metadata,
            )
        else:
            await self._store.record_turn(
                thread_id=thread_id,
                turn_id=turn_id,
                question=question,
                answer=result.answer,
                citations=result.citations,
                metadata=result.metadata,
            )
        return result

    async def _load_history(self, thread_id: str) -> list[Message]:
        """Read last N user/assistant turns for this thread, chronologically.

        ``list_turns`` returns newest-first; we reverse so the prompt sees
        turns in the order they happened (older → newer).
        """
        rows = await self._store.list_turns(thread_id=thread_id, limit=self._max_history_turns)
        out: list[Message] = []
        for row in reversed(rows):
            out.append(Message(role="user", content=row["question"]))
            out.append(Message(role="assistant", content=row["answer"]))
        return out

    async def _load_accumulated(self, thread_id: str) -> list[Evidence]:
        """v0.1 policy: reuse the immediately previous turn's evidence pool.

        Pool was persisted as ``metadata["_evidence_pool"]`` in
        ``record_turn``. Returns ``[]`` when no prior turn exists OR when the
        data is malformed (defensive — a corrupted row shouldn't break the
        new turn).
        """
        rows = await self._store.list_turns(thread_id=thread_id, limit=1)
        if not rows:
            return []
        raw_meta = rows[0].get("metadata")
        if not isinstance(raw_meta, dict):
            return []
        meta = cast(dict[str, Any], raw_meta)
        pool_raw = meta.get("_evidence_pool")
        if not isinstance(pool_raw, list):
            return []
        items = cast(list[Any], pool_raw)
        out: list[Evidence] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # Copy so we don't mutate the caller's dict via the .pop in deserializer.
            ev = _deserialize_evidence(dict(cast(dict[str, Any], item)))
            if ev is not None:
                out.append(ev)
        return out

    def _package_result(
        self,
        final: AgentState,
        thread_id: str,
        turn_id: str,
    ) -> TurnResult:
        answer = final.get("answer_text", "")
        pool = final.get("evidence_pool", [])
        citations = build_citations(answer, pool)
        unsupported = count_unsupported_markers(answer, {e.evidence_id for e in pool})

        verifier: dict[str, Any] | None = None
        sig = final.get("verifier_signal")
        if isinstance(sig, VerifierSignal):
            verifier = sig.model_dump()

        routes = sorted(final.get("routes_taken") or set())
        routing: dict[str, Any] = {
            "subqueries_count": len(final.get("subqueries") or []),
            "routes_taken": routes,
            "evidence_pool_size": len(pool),
            # If subqueries was empty, every evidence in the pool came from
            # accumulated_evidence — that's the reuse contribution count.
            "evidence_reused_count": (len(pool) if not final.get("subqueries") else 0),
        }

        tokens_obj = final.get("tokens") or TokenUsage()
        tokens = tokens_obj.model_dump()

        metadata: dict[str, Any] = {
            "routing": routing,
            "verifier": verifier,
            "tokens": tokens,
            "latency_per_node": dict(final.get("latency_per_node") or {}),
            "unsupported_markers": unsupported,
            # Internal: needed by ``_load_accumulated`` to make reuse turns
            # work. Underscore-prefixed key so it stays out of the public
            # response shape in Plano 4's API (the API filter strips
            # ``_``-prefixed keys before serializing).
            "_evidence_pool": [_serialize_evidence(e) for e in pool],
        }

        return TurnResult(
            thread_id=thread_id,
            turn_id=turn_id,
            answer=answer,
            citations=[
                {
                    "evidence_id": c.evidence_id,
                    "kind": c.kind,
                    "source": c.source,
                    "section": c.section,
                    "snippet": c.snippet,
                }
                for c in citations
            ],
            metadata=metadata,
        )


def _serialize_evidence(e: Evidence) -> dict[str, Any]:
    """Pydantic dump with a ``__kind__`` discriminator so the orchestrator can
    rebuild ``SqlEvidence`` vs ``TextEvidence`` on load. Row values that aren't
    JSON-native (date, Decimal, UUID, bytes) are stringified — fidelity isn't
    required, the pool is just round-tripped for next-turn citations.
    """
    data = e.model_dump()
    if isinstance(e, SqlEvidence):
        data["__kind__"] = "sql"
        # Coerce non-JSON-native row values to str so json.dumps survives.
        row_raw = data.get("row", [])
        if isinstance(row_raw, (list, tuple)):
            row_seq = cast(list[Any] | tuple[Any, ...], row_raw)
            data["row"] = [_json_safe(v) for v in row_seq]
    else:
        # TextEvidence — verified by union exhaustion (Evidence is the
        # discriminated union of SqlEvidence | TextEvidence).
        data["__kind__"] = "text"
    return data


def _json_safe(value: Any) -> Any:
    """Return a JSON-encodable form of ``value``. Pass through native types;
    fall back to ``str(value)`` for anything else (date, Decimal, UUID, bytes).
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        seq = cast(list[Any] | tuple[Any, ...], value)
        return [_json_safe(v) for v in seq]
    if isinstance(value, dict):
        d = cast(dict[Any, Any], value)
        return {str(k): _json_safe(v) for k, v in d.items()}
    return str(value)


def _deserialize_evidence(data: dict[str, Any]) -> Evidence | None:
    """Inverse of ``_serialize_evidence``. Returns ``None`` on malformed data
    so a corrupted DB row can't crash the multi-turn flow."""
    kind = data.pop("__kind__", None)
    try:
        if kind == "sql":
            # ``row`` may have been JSON-serialized from a tuple to a list.
            row = data.get("row")
            if isinstance(row, list):
                data["row"] = tuple(row)  # pyright: ignore[reportUnknownArgumentType]
            return SqlEvidence.model_validate(data)
        if kind == "text":
            return TextEvidence.model_validate(data)
    except Exception:
        return None
    return None
