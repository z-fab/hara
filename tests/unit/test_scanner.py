"""Tests for the ingest file scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.ingest.scanner import (
    IGNORED_REASONS,
    ScannedFile,
    scan,
)
from hara.utils.exceptions import IngestError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sample_with_subfolder"


def test_scan_classifies_csv_as_sql() -> None:
    files = scan(FIXTURES)
    paths: dict[str, ScannedFile] = {f.relative_path: f for f in files}
    assert "2023/dados.csv" in paths
    assert paths["2023/dados.csv"].target == "sql"


def test_scan_classifies_md_as_vector() -> None:
    files = scan(FIXTURES)
    md = next(f for f in files if f.relative_path == "manual.md")
    assert md.target == "vector"


def test_scan_skips_unknown_extensions_with_reason() -> None:
    files = scan(FIXTURES)
    strange = next(f for f in files if f.relative_path == "strange.xyz")
    assert strange.target == "ignored"
    assert strange.ignore_reason == IGNORED_REASONS["unknown_extension"]


def test_scan_skips_dotfiles() -> None:
    files = scan(FIXTURES)
    paths = [f.relative_path for f in files]
    assert ".hidden.csv" not in paths


def test_scan_orders_results_alphabetically() -> None:
    files = [f for f in scan(FIXTURES) if f.target != "ignored"]
    rels = [f.relative_path for f in files]
    assert rels == sorted(rels)


def test_scan_single_file(tmp_path: Path) -> None:
    p = tmp_path / "single.csv"
    p.write_text("a,b\n1,2\n")
    files = scan(p)
    assert len(files) == 1
    assert files[0].relative_path == "single.csv"


def test_scan_strict_raises_on_unknown(tmp_path: Path) -> None:
    p = tmp_path / "lixo.zzz"
    p.write_text("x")
    with pytest.raises(IngestError):
        scan(p, strict=True)


def test_scan_missing_path(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        scan(tmp_path / "doesnotexist")
