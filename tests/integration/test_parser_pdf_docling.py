"""Integration test for the PDF parser (requires the ``docling`` extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("docling")

from hara.ingest.parsers.pdf import parse_pdf

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.mark.integration
def test_parse_pdf_returns_text_with_heading() -> None:
    text, n = parse_pdf(FIXTURES / "sample_min.pdf")
    assert n > 0
    assert "Manual de Silvicultura" in text or "Silvicultura" in text
