"""Unit tests for ChromaDBConnector chunk-ID generation logic.

These run without the ``chromadb`` extra: we stub the
``chromadb.PersistentClient`` so the tests focus exclusively on the ID
shape ``upsert_chunks`` produces, not on any storage behavior.

(See ``tests/contracts/test_vector_chromadb.py`` for end-to-end behavior
against a real chromadb instance.)
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from hara.connectors.vector.base import TextChunk


class _FakeCollection:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] | None = None

    def upsert(self, **kwargs: Any) -> None:
        self.last_kwargs = kwargs


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._collection = _FakeCollection()

    def get_or_create_collection(self, *args: Any, **kwargs: Any) -> _FakeCollection:
        return self._collection

    def heartbeat(self) -> int:
        return 0


@pytest.fixture
def chromadb_stub(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Inject a fake ``chromadb`` module before importing the connector.

    The connector module-imports ``chromadb`` at top level, so we install
    a stub before the import below. If ``chromadb`` is already imported
    (real extra installed), we still patch ``PersistentClient`` to avoid
    any real I/O.
    """
    fake = types.ModuleType("chromadb")
    fake.PersistentClient = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "chromadb", fake)
    return fake


async def test_chunk_ids_distinguish_repeated_content_by_index(
    chromadb_stub: types.ModuleType, tmp_path: Path
) -> None:
    """Two chunks with identical content but different ``chunk_index`` must
    upsert under distinct IDs.

    Regression: the previous ID was ``f"{file_id}_{content_hash}"``, so
    repeated boilerplate (or chunker overlap) collapsed two distinct
    logical chunks onto the same row, while ``session_store`` recorded
    ``chunks_count=N``. Counts diverged silently.
    """
    # Re-import after the stub is installed.
    if "hara.connectors.vector.chromadb" in sys.modules:
        del sys.modules["hara.connectors.vector.chromadb"]
    from hara.connectors.vector.chromadb import ChromaDBConnector  # noqa: PLC0415

    class _NoopEmbedder:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 3 for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.0] * 3

    conn = ChromaDBConnector(persist_directory=tmp_path, embedder=_NoopEmbedder())  # type: ignore[arg-type]
    await conn.upsert_chunks(
        [
            TextChunk(
                file_id="doc_dup",
                content="boilerplate header",
                metadata={"chunk_index": 0},
            ),
            TextChunk(
                file_id="doc_dup",
                content="boilerplate header",
                metadata={"chunk_index": 1},
            ),
        ]
    )
    captured = conn._collection.last_kwargs  # type: ignore[attr-defined]
    assert captured is not None
    ids: list[str] = list(captured["ids"])
    assert len(ids) == 2, f"expected 2 ids, got {len(ids)}: {ids}"
    assert ids[0] != ids[1], (
        f"chunk IDs must differ when chunk_index differs even if content is identical; got {ids}"
    )
    # Sanity: both IDs should still encode the file_id prefix.
    assert all(i.startswith("doc_dup_") for i in ids)


async def test_chunk_ids_default_index_zero_when_metadata_missing(
    chromadb_stub: types.ModuleType, tmp_path: Path
) -> None:
    """Callers that don't set ``chunk_index`` (legacy/tests) default to 0.

    We only assert the call succeeds and produces a single, well-formed ID;
    older paths predate the chunker's metadata convention.
    """
    if "hara.connectors.vector.chromadb" in sys.modules:
        del sys.modules["hara.connectors.vector.chromadb"]
    from hara.connectors.vector.chromadb import ChromaDBConnector  # noqa: PLC0415

    class _NoopEmbedder:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 3 for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.0] * 3

    conn = ChromaDBConnector(persist_directory=tmp_path, embedder=_NoopEmbedder())  # type: ignore[arg-type]
    await conn.upsert_chunks([TextChunk(file_id="legacy", content="some text", metadata={})])
    captured = conn._collection.last_kwargs  # type: ignore[attr-defined]
    assert captured is not None
    ids: list[str] = list(captured["ids"])
    assert len(ids) == 1
    assert ids[0].startswith("legacy_0_")


def test_sanitize_metadata_drops_empty_lists(chromadb_stub: types.ModuleType) -> None:
    """Empty-list metadata values must be stripped — Chroma rejects them."""
    if "hara.connectors.vector.chromadb" in sys.modules:
        del sys.modules["hara.connectors.vector.chromadb"]
    from hara.connectors.vector.chromadb import _sanitize_metadata  # noqa: PLC0415

    assert _sanitize_metadata({"a": 1, "b": []}) == {"a": 1}
    assert _sanitize_metadata({"a": [1], "b": []}) == {"a": [1]}
    assert _sanitize_metadata({"a": "", "b": [], "c": 0}) == {"a": "", "c": 0}


async def test_upsert_drops_empty_list_metadata(
    chromadb_stub: types.ModuleType, tmp_path: Path
) -> None:
    """``upsert_chunks`` must sanitize chunk metadata before passing to Chroma.

    Regression: PDFs without markdown headings used to produce
    ``heading_path=[]`` and Chroma rejected the upsert with a ValueError.
    """
    if "hara.connectors.vector.chromadb" in sys.modules:
        del sys.modules["hara.connectors.vector.chromadb"]
    from hara.connectors.vector.chromadb import ChromaDBConnector  # noqa: PLC0415

    class _NoopEmbedder:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 3 for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return [0.0] * 3

    conn = ChromaDBConnector(persist_directory=tmp_path, embedder=_NoopEmbedder())  # type: ignore[arg-type]
    await conn.upsert_chunks(
        [
            TextChunk(
                file_id="doc",
                content="text",
                metadata={"chunk_index": 0, "heading_path": [], "section": ""},
            )
        ]
    )
    captured = conn._collection.last_kwargs  # type: ignore[attr-defined]
    assert captured is not None
    metadatas: list[dict[str, Any]] = list(captured["metadatas"])
    assert len(metadatas) == 1
    # Empty list dropped; other keys preserved (including empty string section).
    assert "heading_path" not in metadatas[0]
    assert metadatas[0].get("section") == ""
    assert metadatas[0].get("chunk_index") == 0
    assert metadatas[0].get("file_id") == "doc"
