"""Tests for the agent setup helpers (CLI plumbing)."""

from __future__ import annotations

from pathlib import Path

from hara.agent.setup import load_semantic_maps


def test_load_semantic_maps_both_present(tmp_path: Path) -> None:
    (tmp_path / "structured.yaml").write_text("tables: [a]\n")
    (tmp_path / "unstructured.yaml").write_text("documents: [b]\n")
    s, u = load_semantic_maps(tmp_path)
    assert "tables" in s
    assert "documents" in u


def test_load_semantic_maps_missing_returns_empty(tmp_path: Path) -> None:
    s, u = load_semantic_maps(tmp_path)
    assert s == ""
    assert u == ""


def test_load_semantic_maps_partial(tmp_path: Path) -> None:
    (tmp_path / "structured.yaml").write_text("tables: []\n")
    s, u = load_semantic_maps(tmp_path)
    assert "tables" in s
    assert u == ""
