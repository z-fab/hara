"""Tests for semantic-map YAML dumping and state lock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hara.connectors.sql.base import ColumnStatistics
from hara.services.semantic_map import (
    _stats_to_dict,
    current_snapshot,
    dump_structured_map,
    dump_unstructured_map,
    load_state,
    save_state,
    should_regenerate,
)
from hara.services.session_store import IngestedFileRecord, SessionStore


def test_stats_to_dict_renders_all_fields() -> None:
    s = ColumnStatistics(
        row_count=10,
        null_percentage=20.5,
        min=1.0,
        max=99.0,
        mean=42.0,
        std_dev=12.3,
        median=42.5,
        distinct_count=8,
        top_values=["a", "b"],
        all_unique_values=["a", "b", "c"],
    )
    out = _stats_to_dict(s)
    assert out["row_count"] == 10
    assert out["null_percentage"] == "20.5%"
    assert out["min"] == 1.0
    assert out["mean"] == 42.0
    assert out["std_dev"] == 12.3
    assert out["median"] == 42.5
    assert out["distinct_count"] == 8
    assert out["top_values"] == ["a", "b"]
    assert out["all_unique_values"] == ["a", "b", "c"]


def test_dump_and_load_structured_yaml(tmp_path: Path) -> None:
    out = tmp_path / "structured.yaml"
    dump_structured_map(
        {"tables": [{"table_name": "x", "description": "foo", "columns": []}]},
        out,
    )
    # ``dump_unstructured_map`` is exposed as an alias to keep the public surface
    # symmetric; smoke-test it here so it's covered too.
    out_u = tmp_path / "unstructured.yaml"
    dump_unstructured_map({"documents": []}, out_u)
    assert out.exists()
    assert out_u.exists()
    txt = out.read_text()
    assert "table_name" in txt
    assert "x" in txt


def test_state_round_trip(tmp_path: Path) -> None:
    state_file = tmp_path / ".semantic_map_state.json"
    snapshot = {("abc", "sql", "table_a"), ("def", "vector", "doc_d")}
    save_state(state_file, snapshot)
    loaded = load_state(state_file)
    assert loaded == snapshot


def test_state_missing_returns_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.json") == set()


def test_state_legacy_2tuple_format_treated_as_empty(tmp_path: Path) -> None:
    """Old state files (pre-fix) lacked ``table_or_file_id``; tolerate them
    by returning an empty snapshot — forces a regen, which is safe."""
    state_file = tmp_path / "legacy.json"
    state_file.write_text(json.dumps([{"content_hash": "abc", "target": "sql"}]))
    assert load_state(state_file) == set()


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path / 'a.db'}")
    await s.initialize()
    return s


async def test_should_regenerate_true_on_first_run(tmp_path: Path, store: SessionStore) -> None:
    state = tmp_path / "state.json"
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="x" * 64,
            target="sql",
            relative_path="a.csv",
            table_or_file_id="a",
            rows_count=1,
        )
    )
    assert await should_regenerate(store, state) is True


async def test_snapshot_includes_table_or_file_id(
    store: SessionStore,
) -> None:
    """Two rows sharing the same ``content_hash`` but different
    ``table_or_file_id`` (e.g. file moved/renamed before old row deleted)
    must produce TWO snapshot entries — otherwise the snapshot would
    silently merge them and miss the logical-rename case.
    """
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="z" * 64,
            target="sql",
            relative_path="dados_2024.csv",
            table_or_file_id="dados_2024",
            rows_count=1,
        )
    )
    # Manually emulate two rows with same content_hash but distinct
    # table_or_file_id by inserting directly via a different target+id.
    # Since (content_hash, target) is the PK we use the vector target here
    # to represent the "two distinct logical ids share a hash" condition.
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="z" * 64,
            target="vector",
            relative_path="dados_2024.csv",
            table_or_file_id="dados_2024_v",
            rows_count=1,
        )
    )
    snap = await current_snapshot(store)
    assert len(snap) == 2
    # Each tuple must carry the logical id so renames are detectable.
    ids = {entry[2] for entry in snap}
    assert ids == {"dados_2024", "dados_2024_v"}


async def test_should_regenerate_on_logical_rename(tmp_path: Path, store: SessionStore) -> None:
    """File bytes unchanged but logical id changed (rename case): the
    snapshot must differ from the saved state, forcing a regen."""
    state = tmp_path / "state.json"
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="r" * 64,
            target="sql",
            relative_path="dados_2024.csv",
            table_or_file_id="dados_2024",
            rows_count=1,
        )
    )
    save_state(state, await current_snapshot(store))
    # Same bytes, new logical id (rename: dados_2024 -> dados_anuais).
    await store.delete_ingested_file(content_hash="r" * 64, target="sql")
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="r" * 64,
            target="sql",
            relative_path="dados_anuais.csv",
            table_or_file_id="dados_anuais",
            rows_count=1,
        )
    )
    assert await should_regenerate(store, state) is True


async def test_should_regenerate_false_when_snapshot_matches(
    tmp_path: Path, store: SessionStore
) -> None:
    state = tmp_path / "state.json"
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="x" * 64,
            target="sql",
            relative_path="a.csv",
            table_or_file_id="a",
            rows_count=1,
        )
    )
    snap = await current_snapshot(store)
    save_state(state, snap)
    assert await should_regenerate(store, state) is False


async def test_should_regenerate_true_when_new_file_added(
    tmp_path: Path, store: SessionStore
) -> None:
    state = tmp_path / "state.json"
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="x" * 64,
            target="sql",
            relative_path="a.csv",
            table_or_file_id="a",
            rows_count=1,
        )
    )
    save_state(state, await current_snapshot(store))
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="y" * 64,
            target="sql",
            relative_path="b.csv",
            table_or_file_id="b",
            rows_count=1,
        )
    )
    assert await should_regenerate(store, state) is True
