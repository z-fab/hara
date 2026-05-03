"""Integration test for the DOCX parser (requires the `docling` extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("docling")

from hara.ingest.parsers.docx import parse_docx

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.integration
def test_parse_docx_returns_text() -> None:
    text, n = parse_docx(FIXTURES / "sample_min.docx")
    assert n > 0
    assert "pragas" in text.lower()
