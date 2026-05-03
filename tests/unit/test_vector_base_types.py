"""Tests for Vector connector base types."""

from __future__ import annotations

import pytest

from hara.connectors.vector.base import (
    DocumentInfo,
    TextChunk,
    VectorConnector,
)


def test_text_chunk_minimum_fields() -> None:
    chunk = TextChunk(file_id="doc1", content="hello", metadata={"page": 1})
    assert chunk.file_id == "doc1"
    assert chunk.content == "hello"
    assert chunk.embedding is None


def test_text_chunk_with_embedding() -> None:
    chunk = TextChunk(file_id="doc1", content="hello", metadata={}, embedding=[0.1, 0.2, 0.3])
    assert chunk.embedding == [0.1, 0.2, 0.3]


def test_document_info() -> None:
    doc = DocumentInfo(file_id="doc1", chunk_count=12)
    assert doc.file_id == "doc1"
    assert doc.chunk_count == 12


def test_vector_connector_is_abstract() -> None:
    assert hasattr(VectorConnector, "__abstractmethods__")
    with pytest.raises(TypeError):
        VectorConnector()  # type: ignore[abstract]
