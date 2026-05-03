"""SQL Executor node — generate, validate, execute SQL with retry.

Three layers per spec §13:
  Camada 1: sqlglot AST (validate_and_inject_limit) — implemented here.
  Camada 2: read_only=True at the connector — passed through.
  Camada 3: separate DB role — operator's responsibility (docs).
"""

from __future__ import annotations

import logging
from typing import Any, cast

import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.llm_invoke import extract_text
from hara.agent.prompts.sql import build_sql_prompt, strip_sql_fences
from hara.agent.sql_safety import SqlValidationError, validate_and_inject_limit
from hara.agent.state import AgentState, SubQuery, TokenUsage
from hara.agent.tracking import extract_token_usage, measure_node_latency
from hara.connectors.sql.base import SQLConnector

log = logging.getLogger(__name__)


def _extract_allowed_tables(structured_map_yaml: str) -> list[str] | None:
    """Parse structured.yaml to get the set of tables the Planner is allowed
    to query. Returns None when YAML is empty or malformed — caller treats
    None as "no allowlist enforcement" (back-compat for tests / callers
    without a semantic_map).
    """
    if not structured_map_yaml.strip():
        return None
    try:
        data = yaml.safe_load(structured_map_yaml)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    data_dict = cast(dict[str, Any], data)
    tables_raw: Any = data_dict.get("tables") or []
    if not isinstance(tables_raw, list):
        return None
    tables_list = cast(list[Any], tables_raw)
    out: list[str] = []
    for t in tables_list:
        if isinstance(t, dict):
            t_dict = cast(dict[str, Any], t)
            name = t_dict.get("table_name") or t_dict.get("name")
            if isinstance(name, str):
                out.append(name)
    return out or None


async def sql_executor_node(
    state: AgentState,
    *,
    llm: BaseChatModel,
    connector: SQLConnector,
    structured_map_yaml: str,
    max_retries: int,
    max_rows: int,
) -> dict[str, Any]:
    """Iterate over sql-typed subqueries, run with retry. Returns state delta."""
    sql_subqueries = [sq for sq in state.get("subqueries", []) if sq.type == "sql"]

    results: list[dict[str, Any]] = []
    total_tokens = TokenUsage()

    with measure_node_latency() as timer:
        for sq in sql_subqueries:
            entry, sub_tokens = await _run_one(
                sq, llm, connector, structured_map_yaml, max_retries, max_rows
            )
            results.append(entry)
            total_tokens = TokenUsage(
                input=total_tokens.input + sub_tokens.input,
                output=total_tokens.output + sub_tokens.output,
                total=total_tokens.total + sub_tokens.total,
            )

    return {
        "sql_results": results,
        "tokens": total_tokens,
        "latency_per_node": {"sql_executor": timer.elapsed},
    }


async def _run_one(
    sq: SubQuery,
    llm: BaseChatModel,
    connector: SQLConnector,
    structured_map_yaml: str,
    max_retries: int,
    max_rows: int,
) -> tuple[dict[str, Any], TokenUsage]:
    """One SQL subquery with retry loop."""
    allowed = _extract_allowed_tables(structured_map_yaml)
    last_error = ""
    accumulated = TokenUsage()
    for attempt in range(max_retries):
        prompt = build_sql_prompt(
            question=sq.question,
            dialect=connector.dialect,
            structured_map_yaml=structured_map_yaml,
            previous_error=last_error or None,
        )
        try:
            msg = await llm.ainvoke(prompt)
        except Exception as e:
            last_error = f"LLM call failed: {e}"
            log.warning("sql node attempt %d: LLM call failed: %s", attempt + 1, e)
            continue

        usage = extract_token_usage(msg)
        accumulated = TokenUsage(
            input=accumulated.input + usage.input,
            output=accumulated.output + usage.output,
            total=accumulated.total + usage.total,
        )

        # Use extract_text: providers like Gemini may return `content` as a
        # list of typed blocks ([{"type":"text","text":"..."}]) instead of
        # a plain string; passing a list to strip_sql_fences crashes.
        raw_sql = strip_sql_fences(extract_text(msg)).strip()

        try:
            sanitized, tables = validate_and_inject_limit(
                raw_sql,
                dialect=connector.dialect,
                max_rows=max_rows,
                allowed_tables=allowed,
            )
        except SqlValidationError as e:
            # Fail fast on validation errors per spec §13 — re-prompting an LLM
            # that just emitted a non-SELECT/dangerous-fn won't make the next
            # response safer. We surface the violation to the Synthesizer.
            last_error = f"SQL validation: {e}"
            log.warning("sql node attempt %d: validation failed: %s", attempt + 1, e)
            break

        try:
            result = await connector.execute_query(sanitized, read_only=True)
        except Exception as e:
            last_error = f"Execution: {e}"
            log.warning("sql node attempt %d: exec failed: %s", attempt + 1, e)
            continue

        return (
            {
                "task_query": sq.question,
                "sql_query": sanitized,
                "tables": tables,
                "columns": list(result.columns),
                "rows": list(result.rows),
                "executed": True,
                "error": "",
            },
            accumulated,
        )

    return (
        {
            "task_query": sq.question,
            "sql_query": "",
            "tables": [],
            "columns": [],
            "rows": [],
            "executed": False,
            "error": last_error or "exhausted retries",
        },
        accumulated,
    )
