"""AgentState + Pydantic models that flow through the LangGraph pipeline.

The state is a TypedDict (LangGraph contract). Sub-pieces are Pydantic
models so we get validation at the boundaries (LLM outputs, multi-turn
loading from session_store).

Naming reflects the spec §3 — `subqueries` (not "search_tasks"),
`evidence_pool` (not "context"), `accumulated_evidence` (not "history_evidence").
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


class Message(BaseModel):
    """One turn in the multi-turn history (user or assistant)."""

    role: Literal["user", "assistant"]
    content: str


class SubQuery(BaseModel):
    """One sub-task emitted by the Planner.

    `id` is opaque (e.g. "sq_1") — used by the Planner for self-reference
    in refinement loops (deferred to v0.2) and by traces. The agent doesn't
    need it for current routing.
    """

    id: str
    type: Literal["sql", "text"]
    question: str
    sources: list[str] = Field(default_factory=list[str])


class SqlEvidence(BaseModel):
    """One row from a SQL retrieval, lifted to evidence-pool form.

    `evidence_id` is the integer N that appears as `<ref:N>` in the
    Synthesizer's output. Allocated by the orchestrator from a single
    counter spanning sql + text evidence.
    """

    evidence_id: int
    source_table: str
    columns: list[str]
    row: tuple[Any, ...]
    sql_query: str = ""


class TextEvidence(BaseModel):
    """One vector chunk lifted to evidence-pool form."""

    evidence_id: int
    file_id: str
    content: str
    section: str = ""
    score: float | None = None


# Discriminated union — LangChain's Pydantic v2 supports both shapes via
# isinstance checks. We don't need a tagged union here because the orchestrator
# always knows which kind it stored. Type alias keeps signatures readable.
Evidence = SqlEvidence | TextEvidence


class VerifierSignal(BaseModel):
    """Post-hoc quality score from the Verifier (when [verifier].mode = signal).

    Goes to metadata.verifier; never blocks the response.
    """

    overall_pass: bool
    pct_supported: float = Field(ge=0.0, le=1.0)
    n_missing_aspects: int = Field(ge=0)
    weak_sentences: list[str] = Field(default_factory=list[str])


class TokenUsage(BaseModel):
    """Sum of LLM tokens across the turn. Reducer-aggregated."""

    input: int = 0
    output: int = 0
    total: int = 0


# ---------- AgentState (the LangGraph TypedDict) ----------


def _add_token_usage(left: TokenUsage | None, right: TokenUsage | None) -> TokenUsage:
    """Reducer for `tokens`. Sums per-node usage as nodes complete."""
    a = left or TokenUsage()
    b = right or TokenUsage()
    return TokenUsage(
        input=a.input + b.input,
        output=a.output + b.output,
        total=a.total + b.total,
    )


def _merge_latency(
    left: dict[str, float] | None, right: dict[str, float] | None
) -> dict[str, float]:
    """Reducer for `latency_per_node`. Last-write-wins per node."""
    out: dict[str, float] = dict(left or {})
    out.update(right or {})
    return out


class AgentState(TypedDict, total=False):
    """LangGraph state. `total=False` so partial dicts (per-node returns) are valid."""

    # Input
    question: str
    thread_id: str
    turn_id: str

    # Multi-turno
    history: list[Message]
    accumulated_evidence: list[Evidence]

    # Planner output
    subqueries: list[SubQuery]
    routes_taken: set[str]  # {"sql", "text"} or empty for reuse

    # Retrieval
    sql_results: list[dict[str, Any]]
    text_results: list[dict[str, Any]]

    # Synthesizer output
    answer_text: str
    evidence_pool: list[Evidence]

    # Verifier output
    verifier_signal: VerifierSignal | None

    # Tracking (reducers above)
    tokens: Annotated[TokenUsage, _add_token_usage]
    latency_per_node: Annotated[dict[str, float], _merge_latency]
