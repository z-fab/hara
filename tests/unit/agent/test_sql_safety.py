"""Tests for SQL safety: sqlglot AST validation + LIMIT injection."""

from __future__ import annotations

import pytest

from hara.agent.sql_safety import (
    SqlValidationError,
    validate_and_inject_limit,
)


def test_simple_select_passes_and_gets_limit() -> None:
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao WHERE uf = 'MT'", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 100" in sql.upper()


def test_select_with_existing_limit_keeps_user_value() -> None:
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao LIMIT 5", dialect="sqlite", max_rows=100
    )
    # Não tocamos no LIMIT se já existir
    assert "LIMIT 5" in sql or "LIMIT  5" in sql
    assert "LIMIT 100" not in sql.upper()


def test_cte_with_select_passes() -> None:
    sql_in = "WITH t AS (SELECT 1 AS x) SELECT * FROM t"
    sql, _ = validate_and_inject_limit(sql_in, dialect="sqlite", max_rows=100)
    assert "LIMIT 100" in sql.upper()


def test_insert_rejected() -> None:
    with pytest.raises(SqlValidationError, match="only SELECT"):
        validate_and_inject_limit("INSERT INTO producao VALUES (1)", dialect="sqlite", max_rows=100)


def test_update_rejected() -> None:
    with pytest.raises(SqlValidationError, match="only SELECT"):
        validate_and_inject_limit("UPDATE producao SET x = 1", dialect="sqlite", max_rows=100)


def test_delete_rejected() -> None:
    with pytest.raises(SqlValidationError, match="only SELECT"):
        validate_and_inject_limit("DELETE FROM producao WHERE 1=1", dialect="sqlite", max_rows=100)


def test_drop_rejected() -> None:
    with pytest.raises(SqlValidationError, match="only SELECT"):
        validate_and_inject_limit("DROP TABLE producao", dialect="sqlite", max_rows=100)


def test_multi_statement_rejected() -> None:
    with pytest.raises(SqlValidationError, match="single statement"):
        validate_and_inject_limit("SELECT 1; SELECT 2", dialect="sqlite", max_rows=100)


def test_multi_statement_with_drop_rejected() -> None:
    """Classic injection: drop tucked behind a benign SELECT."""
    with pytest.raises(SqlValidationError):
        validate_and_inject_limit("SELECT 1; DROP TABLE producao", dialect="sqlite", max_rows=100)


def test_unparseable_sql_rejected() -> None:
    with pytest.raises(SqlValidationError, match="parse"):
        validate_and_inject_limit("SELEKT * FROM x", dialect="sqlite", max_rows=100)


def test_postgres_dangerous_function_rejected() -> None:
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit(
            "SELECT pg_read_file('/etc/passwd')", dialect="postgres", max_rows=100
        )


def test_postgres_lo_import_rejected() -> None:
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit("SELECT lo_import('/tmp/x')", dialect="postgres", max_rows=100)


def test_sqlite_load_extension_rejected() -> None:
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit("SELECT load_extension('x')", dialect="sqlite", max_rows=100)


def test_validate_returns_tables_referenced() -> None:
    """Caller (SQL Executor node) needs the tables hit, for tracking."""
    _, tables = validate_and_inject_limit(
        "SELECT p.uf FROM producao p JOIN clima c ON c.uf = p.uf",
        dialect="sqlite",
        max_rows=100,
    )
    assert tables == ["clima", "producao"]  # sorted


def test_query_too_long_rejected() -> None:
    """Defense-in-depth against pathological prompts."""
    big = "SELECT 1 " + " ".join(f", {i} AS c{i}" for i in range(2000))
    with pytest.raises(SqlValidationError, match="too long"):
        validate_and_inject_limit(big, dialect="sqlite", max_rows=100)


def test_cte_with_delete_dml_rejected() -> None:
    """CTE-wrapped DELETE must not slip past Camada 1."""
    with pytest.raises(SqlValidationError, match="DML inside query"):
        validate_and_inject_limit(
            "WITH t AS (DELETE FROM producao RETURNING *) SELECT * FROM t",
            dialect="postgres",
            max_rows=100,
        )


def test_cte_with_update_dml_rejected() -> None:
    with pytest.raises(SqlValidationError, match="DML inside query"):
        validate_and_inject_limit(
            "WITH t AS (UPDATE producao SET tons=0 RETURNING *) SELECT * FROM t",
            dialect="postgres",
            max_rows=100,
        )


def test_cte_with_insert_dml_rejected() -> None:
    with pytest.raises(SqlValidationError, match="DML inside query"):
        validate_and_inject_limit(
            "WITH t AS (INSERT INTO producao VALUES ('MT', 1) RETURNING *) SELECT * FROM t",
            dialect="postgres",
            max_rows=100,
        )


def test_union_gets_limit_injected() -> None:
    """Spec §13: LIMIT injected when absent — applies to UNION too."""
    sql, _ = validate_and_inject_limit("SELECT 1 UNION SELECT 2", dialect="sqlite", max_rows=100)
    assert "LIMIT 100" in sql.upper()


def test_union_with_existing_limit_preserved() -> None:
    sql, _ = validate_and_inject_limit(
        "SELECT 1 UNION SELECT 2 LIMIT 5", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 5" in sql
    assert "LIMIT 100" not in sql.upper()


def test_mssql_xp_cmdshell_rejected() -> None:
    # sqlglot's canonical dialect name for MSSQL / SQL Server is "tsql".
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit("SELECT xp_cmdshell('dir')", dialect="tsql", max_rows=100)


def test_mysql_load_file_rejected() -> None:
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit("SELECT load_file('/etc/passwd')", dialect="mysql", max_rows=100)


def test_dangerous_function_uppercase_rejected() -> None:
    """Case-insensitivity codified — LLMs may emit upper-case."""
    with pytest.raises(SqlValidationError, match="dangerous function"):
        validate_and_inject_limit(
            "SELECT PG_READ_FILE('/etc/passwd')", dialect="postgres", max_rows=100
        )


def test_existing_limit_under_max_kept() -> None:
    """LIMIT smaller than max_rows is preserved (already covered, but explicit)."""
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao LIMIT 5", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 5" in sql
    assert "LIMIT 100" not in sql.upper()


def test_existing_limit_over_max_clamped() -> None:
    """LIMIT bigger than max_rows is clamped to max_rows (Codex P2 fix)."""
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao LIMIT 1000000", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 100" in sql.upper()
    assert "LIMIT 1000000" not in sql


def test_existing_limit_equal_to_max_kept() -> None:
    """LIMIT == max_rows: no change."""
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao LIMIT 100", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 100" in sql.upper()


def test_union_with_limit_over_max_clamped() -> None:
    """UNION + over-max LIMIT also clamped (parallel to Select case)."""
    sql, _ = validate_and_inject_limit(
        "SELECT 1 UNION SELECT 2 LIMIT 5000", dialect="sqlite", max_rows=100
    )
    assert "LIMIT 100" in sql.upper()
    assert "LIMIT 5000" not in sql


def test_allowlist_accepts_listed_table() -> None:
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM producao",
        dialect="sqlite",
        max_rows=100,
        allowed_tables=["producao"],
    )
    assert "LIMIT 100" in sql.upper()


def test_allowlist_rejects_unlisted_table() -> None:
    with pytest.raises(SqlValidationError, match="not in semantic_map allowlist"):
        validate_and_inject_limit(
            "SELECT * FROM hara_turns",
            dialect="sqlite",
            max_rows=100,
            allowed_tables=["producao"],
        )


def test_allowlist_rejects_join_with_unlisted_table() -> None:
    """JOIN with a forbidden table is also rejected."""
    with pytest.raises(SqlValidationError, match="not in semantic_map allowlist"):
        validate_and_inject_limit(
            "SELECT p.uf FROM producao p JOIN hara_turns t ON t.id = p.id",
            dialect="sqlite",
            max_rows=100,
            allowed_tables=["producao"],
        )


def test_allowlist_case_insensitive() -> None:
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM Producao",
        dialect="sqlite",
        max_rows=100,
        allowed_tables=["producao"],
    )
    assert sql  # passes — comparison normalized to lower-case


def test_allowlist_none_means_no_enforcement() -> None:
    """Back-compat: when allowed_tables=None, existing tables-not-in-map
    behavior is preserved (tests pass without setting allowlist)."""
    sql, _ = validate_and_inject_limit(
        "SELECT * FROM anything",
        dialect="sqlite",
        max_rows=100,
        allowed_tables=None,
    )
    assert "LIMIT 100" in sql.upper()
