"""Shared per-column statistics helpers for SQL connectors.

Lives next to ``base.py`` so any SQL connector can reuse the same polars-based
computation by passing in a ``polars.Series``. The base contract
(``SQLConnector.list_tables``) returns ``TableInfo.column_statistics`` —
populating it is optional per spec §4 ("connector preenche se conseguir
baratamente"). All built-in connectors (sqlite, memory, postgres) delegate
here so the YAML emitted by ``hara semantic-map`` is shape-equivalent
across backends.

This module is the canonical place for new SQL connectors to plug into.
A typical implementation:

.. code-block:: python

    df = pl.DataFrame(rows_from_my_backend)
    return {col: calculate_column_stats(df[col]) for col in df.columns}

The helper handles dtype dispatch (numeric vs non-numeric), null counting,
and the categorical-hint heuristic for ``all_unique_values``.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from hara.connectors.sql.base import ColumnStatistics

# Emit ``all_unique_values`` only when the set is small enough to be useful
# for the LLM as a categorical hint (mirrors /experimentos behavior).
ALL_UNIQUE_VALUES_THRESHOLD = 20

_TOP_VALUES_LIMIT = 10


def calculate_column_stats(series: pl.Series) -> ColumnStatistics:
    """Per-column stats. Polars handles dtype dispatch.

    Empty series → just ``row_count=0``. Numeric → ``min/max/mean/std/median``.
    Non-numeric → ``distinct_count`` + top-10 + ``all_unique_values`` when the
    cardinality is ≤ :data:`ALL_UNIQUE_VALUES_THRESHOLD`.
    """
    total = len(series)
    if total == 0:
        return ColumnStatistics(row_count=0)

    null_count = series.null_count()
    null_pct = round((null_count / total) * 100, 2)
    clean = series.drop_nulls()

    kw: dict[str, Any] = {
        "row_count": total,
        "null_percentage": null_pct,
    }

    if series.dtype.is_numeric() and len(clean) > 0:
        kw["min"] = float(clean.min())  # pyright: ignore[reportArgumentType]
        kw["max"] = float(clean.max())  # pyright: ignore[reportArgumentType]
        kw["mean"] = round(float(clean.mean()), 2)  # pyright: ignore[reportArgumentType]
        kw["std_dev"] = (
            round(float(clean.std()), 2) if len(clean) > 1 else 0.0  # pyright: ignore[reportArgumentType]
        )
        kw["median"] = round(float(clean.median()), 2)  # pyright: ignore[reportArgumentType]
    else:
        clean_str = clean.cast(pl.Utf8)
        unique_count = clean_str.n_unique()
        kw["distinct_count"] = unique_count
        if len(clean_str) > 0:
            top = (
                clean_str.value_counts()
                .sort("count", descending=True)
                .head(_TOP_VALUES_LIMIT)
                .get_column(clean_str.name)
                .to_list()
            )
            kw["top_values"] = [str(v) for v in top]
        if 0 < unique_count <= ALL_UNIQUE_VALUES_THRESHOLD:
            kw["all_unique_values"] = [str(v) for v in clean_str.unique().sort().to_list()]

    return ColumnStatistics(**kw)
