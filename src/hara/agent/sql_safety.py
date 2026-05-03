"""SQL safety — Camada 1 of the spec §13 three-layer defense.

This module is the only place LLM-generated SQL is parsed. It returns either
a sanitized query (with LIMIT injected if absent) or raises SqlValidationError.
Camada 2 (`read_only=True` on the connector) and Camada 3 (separate DB role)
live in connectors and docs, not here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlglot
import sqlglot.errors
from sqlglot import exp as sqlglot_exp
from sqlglot.expressions.core import Expression as _Expression

_MAX_QUERY_LEN = 8000


# Dangerous functions per dialect. Empty list for dialects we trust under
# read_only + AST validation alone (e.g. mysql); add to it if future dialects
# expose risky surface.
_DANGEROUS_FUNCTIONS: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        {
            "pg_read_file",
            "pg_read_binary_file",
            "pg_ls_dir",
            "lo_import",
            "lo_export",
            "dblink",
            "dblink_exec",
            "copy",  # COPY ... FROM PROGRAM is the well-known RCE vector
        }
    ),
    "sqlite": frozenset(
        {
            "load_extension",
        }
    ),
    # sqlglot's canonical name for the SQL Server / MSSQL dialect is "tsql".
    "tsql": frozenset(
        {
            "xp_cmdshell",
            "openrowset",
            "opendatasource",
        }
    ),
    "mysql": frozenset(
        {
            "load_file",
            "sys_exec",
            "sys_eval",
        }
    ),
}


class SqlValidationError(ValueError):
    """Raised when an LLM-generated SQL fails any safety check.

    Carries the reason for both the SQL Executor's retry logic (so the LLM
    can see what was wrong on the next attempt) and structured logging.
    """


def validate_and_inject_limit(
    sql: str,
    *,
    dialect: str,
    max_rows: int,
    allowed_tables: Sequence[str] | None = None,
) -> tuple[str, list[str]]:
    """Validate `sql` and return `(sanitized_sql, sorted_unique_tables_referenced)`.

    Args:
        sql: Raw SQL string from the LLM.
        dialect: One of the values returned by ``SQLConnector.dialect`` (sqlite,
            postgres, ...).
        max_rows: Inject ``LIMIT max_rows`` if the query has no LIMIT of its own.
        allowed_tables: Optional allowlist of table names. When provided, every
            referenced table MUST be in the set (case-insensitive); otherwise
            ``SqlValidationError`` is raised. When ``None``, no allowlist
            enforcement is performed (back-compat for tests/callers without
            a semantic_map).

    Raises:
        SqlValidationError: On any of these conditions:
            - Query > ``_MAX_QUERY_LEN`` chars (defense against pathological prompts).
            - Parse error.
            - Multiple statements (semicolon-separated).
            - Statement not SELECT/UNION/CTE-with-SELECT.
            - Reference to a function in ``_DANGEROUS_FUNCTIONS[dialect]``.
            - When ``allowed_tables`` is set, any referenced table not in the
              allowlist.
    """
    if len(sql) > _MAX_QUERY_LEN:
        raise SqlValidationError(f"query too long ({len(sql)} > {_MAX_QUERY_LEN})")

    try:
        statements = sqlglot.parse(sql, dialect=dialect)
    except sqlglot.errors.ParseError as e:
        raise SqlValidationError(f"parse error: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlValidationError(f"only single statement allowed; found {len(statements)}")
    stmt = statements[0]

    # Only SELECT (and CTEs ending in a SELECT, and UNIONs of SELECTs).
    # In sqlglot 25+, `WITH ... SELECT` parses as a top-level Select with the
    # CTE attached as the `with_` arg, so the With branch is mostly defensive.
    if not isinstance(stmt, (sqlglot_exp.Select, sqlglot_exp.Union, sqlglot_exp.With)):
        raise SqlValidationError(f"only SELECT/CTE/UNION allowed; got {type(stmt).__name__}")
    if isinstance(stmt, sqlglot_exp.With):
        # The WITH must wrap a SELECT/UNION as its body.
        body = stmt.this
        if not isinstance(body, (sqlglot_exp.Select, sqlglot_exp.Union)):
            raise SqlValidationError("only SELECT-bodied CTE allowed")

    # Defense against CTE-wrapped DML: WITH t AS (DELETE/INSERT/UPDATE ...) SELECT
    # parses as a top-level Select with the DML attached as a CTE expression.
    # Without this check, Camada 1 would let it through and rely entirely on
    # Camada 2 (read_only=True) — contrary to the spec §13 framing of Camada 1
    # as a parser-level gate.
    for forbidden in stmt.find_all(sqlglot_exp.Insert, sqlglot_exp.Update, sqlglot_exp.Delete):
        raise SqlValidationError(f"DML inside query disallowed: {type(forbidden).__name__}")

    # Dangerous functions check (case-insensitive). `Anonymous` extends `Func`,
    # so `find_all(Func)` covers user-defined / unknown functions like
    # `pg_read_file`, `load_extension`, etc.
    dangerous = _DANGEROUS_FUNCTIONS.get(dialect, frozenset())
    if dangerous:
        for fn in stmt.find_all(sqlglot_exp.Func):
            name = (fn.name or "").lower()
            if name in dangerous:
                raise SqlValidationError(f"dangerous function {name!r}")

    tables = sorted({t.name for t in stmt.find_all(sqlglot_exp.Table)})
    if allowed_tables is not None:
        allowed = {t.lower() for t in allowed_tables}
        offending = [t for t in tables if t.lower() not in allowed]
        if offending:
            raise SqlValidationError(f"table(s) not in semantic_map allowlist: {offending}")

    sanitized = _maybe_inject_limit(stmt, max_rows, dialect)
    return sanitized, tables


def _maybe_inject_limit(
    stmt: _Expression,
    max_rows: int,
    dialect: str,
) -> str:
    """Inject or clamp the outermost SELECT/UNION LIMIT to ``max_rows``.

    - No LIMIT: inject ``LIMIT max_rows``.
    - LIMIT N where N <= max_rows: keep as-is.
    - LIMIT N where N > max_rows: replace with ``LIMIT max_rows``.
    - Non-numeric LIMIT (parameterized, expression): leave untouched —
      validation should never reach this branch in practice (we'd have
      no way to compare), but be defensive: re-set to max_rows.
    """
    target: _Expression = stmt
    if isinstance(stmt, sqlglot_exp.With):
        target = stmt.this  # the wrapped SELECT/UNION

    if isinstance(target, (sqlglot_exp.Select, sqlglot_exp.Union)):
        existing = target.args.get("limit")
        if existing is None:
            target.limit(max_rows, copy=False)
        else:
            existing_n = _extract_limit_value(existing)
            if existing_n is None or existing_n > max_rows:
                target.limit(max_rows, copy=False)

    return stmt.sql(dialect=dialect)


def _extract_limit_value(limit_expr: _Expression) -> int | None:
    """Return integer LIMIT value if present and parseable; else None.

    sqlglot's ``Limit`` node exposes the literal expression via ``.expression``
    (sometimes ``.args["expression"]``). When the LLM emits ``LIMIT 100``,
    that's a ``Literal``; when it emits ``LIMIT $1`` (a placeholder) or some
    expression, we conservatively return None so the caller clamps to max_rows.
    """
    expr = limit_expr.args.get("expression") or getattr(limit_expr, "expression", None)
    if expr is None:
        return None
    if isinstance(expr, sqlglot_exp.Literal) and expr.is_int:
        try:
            return int(expr.this)
        except (TypeError, ValueError):
            return None
    return None
