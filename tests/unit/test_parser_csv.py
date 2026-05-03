"""Tests for the CSV parser."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from hara.ingest.parsers.csv import parse_csv
from hara.utils.exceptions import IngestError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_csv_returns_dataframe_with_inferred_types() -> None:
    df, rows = parse_csv(FIXTURES / "sample.csv")
    assert rows == 2
    assert df.columns == ["municipio", "producao_t"]
    assert df["producao_t"].dtype.is_integer()


def test_parse_csv_falls_back_to_latin1_when_utf8_fails() -> None:
    df, rows = parse_csv(FIXTURES / "sample_latin1.csv")
    assert rows == 1
    assert df["cidade"][0] == "São Paulo"


def test_parse_csv_raises_on_malformed_file() -> None:
    with pytest.raises(IngestError) as exc:
        parse_csv(FIXTURES / "sample_broken.csv")
    assert "sample_broken.csv" in str(exc.value)


def test_parse_csv_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(IngestError):
        parse_csv(p)


def test_parse_csv_returns_polars_dataframe(tmp_path: Path) -> None:
    p = tmp_path / "x.csv"
    p.write_text("a,b\n1,2\n")
    df, _ = parse_csv(p)
    assert isinstance(df, pl.DataFrame)
