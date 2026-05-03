"""Tests for the 2-stage chunker (heading + size)."""

from __future__ import annotations

from hara.ingest.chunking import chunk_text


def test_chunk_text_respects_size() -> None:
    text = "para1.\n\n" + ("a" * 1000) + "\n"
    chunks = chunk_text(text, file_id="doc", chunk_size=400, chunk_overlap=50)
    assert all(len(c.content) <= 450 for c in chunks)
    assert sum(len(c.content) for c in chunks) >= 1000


def test_chunk_text_respects_heading_boundary() -> None:
    text = "# Capítulo 1\n\nintro do cap 1.\n\n# Capítulo 2\n\nintro do cap 2.\n"
    chunks = chunk_text(text, file_id="doc", chunk_size=200, chunk_overlap=0)
    sections = [c.metadata.get("section", "") for c in chunks]
    assert "Capítulo 1" in sections
    assert "Capítulo 2" in sections


def test_chunk_text_preserves_nested_headings() -> None:
    text = "# Cap 1\n\nintro\n\n## Seção 1.1\n\ncorpo da seção.\n\n### Sub 1.1.1\n\ndetalhe.\n"
    chunks = chunk_text(text, file_id="d", chunk_size=200, chunk_overlap=0)
    sections = {c.metadata.get("section", "") for c in chunks}
    assert "Cap 1 > Seção 1.1 > Sub 1.1.1" in sections


def test_chunk_text_no_headings_emits_empty_section() -> None:
    """PDFs without markdown headings should emit section='' (Chroma-safe)
    instead of heading_path=[] (which Chroma rejects as empty list)."""
    text = "Texto puro\n\nsem nenhum heading markdown."
    chunks = chunk_text(text, file_id="d", chunk_size=200, chunk_overlap=0)
    assert len(chunks) >= 1
    assert all(c.metadata.get("section") == "" for c in chunks)
    # Also confirm no heading_path key (avoid double-emitting)
    assert all("heading_path" not in c.metadata for c in chunks)


def test_chunk_text_assigns_file_id() -> None:
    chunks = chunk_text("texto.", file_id="my_doc")
    assert all(c.file_id == "my_doc" for c in chunks)


def test_chunk_text_empty_returns_empty_list() -> None:
    assert chunk_text("", file_id="d") == []


def test_chunk_text_overlap_keeps_continuity() -> None:
    text = "abcdefghij" * 20  # 200 chars
    chunks = chunk_text(text, file_id="d", chunk_size=80, chunk_overlap=20)
    # Overlap means the tail of chunk N starts the head of chunk N+1.
    if len(chunks) >= 2:
        tail = chunks[0].content[-20:]
        head = chunks[1].content[:20]
        assert tail == head
