"""Tests for the SQL Executor node."""

from __future__ import annotations

from typing import Any, cast

import polars as pl
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from hara.agent.nodes.sql_executor import sql_executor_node
from hara.agent.state import AgentState, SubQuery
from hara.connectors.sql.memory import MemorySQLConnector


class _SqlFakeLLM:
    """Fake LLM that returns scripted plain-SQL responses (one per LLM call)."""

    def __init__(self, sql_responses: list[Any]) -> None:
        self._responses = list(sql_responses)
        self._idx = 0

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        raise NotImplementedError("SQL Executor uses plain-text LLM")

    async def ainvoke(self, _i: Any) -> Any:
        r = self._responses[self._idx]
        self._idx += 1
        if isinstance(r, Exception):
            raise r
        return AIMessage(content=r) if isinstance(r, str) else r


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


@pytest.fixture
async def memory_sql() -> MemorySQLConnector:
    conn = MemorySQLConnector()
    df = pl.DataFrame({"uf": ["MT", "PR", "SP"], "tons": [100, 80, 50]})
    await conn.upsert_table("producao", df, mode="replace")
    return conn


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


async def test_sql_executor_happy_path(memory_sql: MemorySQLConnector) -> None:
    fake = _SqlFakeLLM(["SELECT uf, tons FROM producao WHERE uf = 'MT'"])
    state = _state([SubQuery(id="sq_1", type="sql", question="quanto produziu MT?")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="tables: [...]",
        max_retries=3,
        max_rows=100,
    )
    assert "sql_results" in delta
    assert len(delta["sql_results"]) == 1
    res = delta["sql_results"][0]
    assert res["executed"] is True
    assert res["columns"] == ["uf", "tons"]
    assert res["rows"] == [("MT", 100)]


async def test_sql_executor_skips_text_subqueries(memory_sql: MemorySQLConnector) -> None:
    """Mixed subquery list: only SQL ones are touched."""
    fake = _SqlFakeLLM(["SELECT uf FROM producao"])
    state = _state(
        [
            SubQuery(id="sq_1", type="text", question="x"),
            SubQuery(id="sq_2", type="sql", question="y"),
        ]
    )
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    assert len(delta["sql_results"]) == 1


async def test_sql_executor_injects_limit(memory_sql: MemorySQLConnector) -> None:
    """Spec §13 — when LLM omits LIMIT, we inject [agent].sql_max_rows."""
    fake = _SqlFakeLLM(["SELECT uf, tons FROM producao"])
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=2,
    )
    res = delta["sql_results"][0]
    # Only 2 rows returned despite table having 3
    assert len(res["rows"]) == 2


async def test_sql_executor_strips_markdown_fence(memory_sql: MemorySQLConnector) -> None:
    """LLM that wraps in ```sql ... ``` should still work."""
    fake = _SqlFakeLLM(["```sql\nSELECT uf FROM producao\n```"])
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    assert delta["sql_results"][0]["executed"] is True


async def test_sql_executor_rejects_non_select(memory_sql: MemorySQLConnector) -> None:
    """sqlglot validation kicks in even before connector. INSERT must fail
    fast — no retry of validation errors per spec §13."""
    fake = _SqlFakeLLM(["INSERT INTO producao VALUES ('X', 1)"])
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    res = delta["sql_results"][0]
    assert res["executed"] is False
    assert "SELECT" in res["error"]


async def test_sql_executor_retries_on_execution_error(
    memory_sql: MemorySQLConnector,
) -> None:
    """First attempt: nonexistent table; retry: correct table."""
    fake = _SqlFakeLLM(
        [
            "SELECT * FROM produkao",  # typo, will fail
            "SELECT uf, tons FROM producao",
        ]
    )
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    res = delta["sql_results"][0]
    assert res["executed"] is True


async def test_sql_executor_gives_up_after_max_retries(
    memory_sql: MemorySQLConnector,
) -> None:
    fake = _SqlFakeLLM(
        [
            "SELECT * FROM nope1",
            "SELECT * FROM nope2",
            "SELECT * FROM nope3",
        ]
    )
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    res = delta["sql_results"][0]
    assert res["executed"] is False
    assert res["error"]


async def test_sql_executor_records_tracking(memory_sql: MemorySQLConnector) -> None:
    fake = _SqlFakeLLM(["SELECT uf FROM producao"])
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    assert "latency_per_node" in delta
    assert "sql_executor" in delta["latency_per_node"]


async def test_sql_executor_empty_subqueries_returns_empty_results(
    memory_sql: MemorySQLConnector,
) -> None:
    """Reuse path: subqueries == [] → nothing to run."""
    fake = _SqlFakeLLM([])
    state = _state([])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml="",
        max_retries=3,
        max_rows=100,
    )
    assert delta["sql_results"] == []


async def test_sql_executor_rejects_table_outside_semantic_map(
    memory_sql: MemorySQLConnector,
) -> None:
    """SQL targeting a table not in structured_map_yaml is rejected by Camada 1.
    Validates the Codex final-review P1 fix."""
    fake = _SqlFakeLLM(["SELECT * FROM hara_turns"])
    state = _state([SubQuery(id="sq_1", type="sql", question="x")])
    delta = await sql_executor_node(
        state,
        llm=_llm(fake),
        connector=memory_sql,
        structured_map_yaml=("tables:\n  - table_name: producao\n    columns: [uf, tons]\n"),
        max_retries=3,
        max_rows=100,
    )
    res = delta["sql_results"][0]
    assert res["executed"] is False
    assert "allowlist" in res["error"]
