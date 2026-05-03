"""Tests for the MD/TXT direct parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.ingest.parsers.text import parse_text
from hara.utils.exceptions import IngestError

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_parse_md_returns_text() -> None:
    text, n = parse_text(FIXTURES / "sample.md")
    assert "Manual" in text
    assert n == len(text)


def test_parse_txt_returns_text() -> None:
    text, _ = parse_text(FIXTURES / "sample.txt")
    assert text.strip() == "apenas texto."


def test_parse_text_falls_back_to_latin1(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    p.write_bytes("São Paulo".encode("latin-1"))
    text, _ = parse_text(p)
    assert "São Paulo" in text


def test_parse_text_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IngestError):
        parse_text(tmp_path / "nope.md")
