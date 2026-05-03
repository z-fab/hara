"""In-memory vector connector. For tests/dev only.

Uses numpy for cosine similarity. Stores chunks with their embeddings in
a Python list. Suitable for small N (< 1000); not for production.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from langchain_core.embeddings import Embeddings

from hara.config.schemas import MemoryVectorConfig, VectorConnectorConfigUnion
from hara.connectors.sql.base import ConnectorHealth
from hara.connectors.vector.base import DocumentInfo, TextChunk, VectorConnector


class MemoryVectorConnector(VectorConnector):
    def __init__(self, embedder: Embeddings) -> None:
        self._embedder = embedder
        self._store: list[TextChunk] = []

    @classmethod
    def from_config(
        cls,
        config: VectorConnectorConfigUnion,
        embedder: Embeddings,
    ) -> MemoryVectorConnector:
        if not isinstance(config, MemoryVectorConfig):
            raise TypeError(f"Expected MemoryVectorConfig, got {type(config).__name__}")
        return cls(embedder=embedder)

    async def similarity_search(
        self,
        query: str,
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        vec = self._embedder.embed_query(query)
        return await self.similarity_search_by_vector(vec, k=k, filter=filter)

    async def similarity_search_by_vector(
        self,
        vector: list[float],
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        candidates = self._filter_chunks(filter)
        if not candidates:
            return []
        target = np.array(vector, dtype=np.float32)
        scored: list[tuple[float, TextChunk]] = []
        for c in candidates:
            if c.embedding is None:
                continue
            v = np.array(c.embedding, dtype=np.float32)
            score = float(_cosine(target, v))
            scored.append((score, c))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _, c in scored[:k]]

    async def get_chunks(
        self,
        file_id: str,
        *,
        include_embeddings: bool = False,
    ) -> list[TextChunk]:
        out: list[TextChunk] = []
        for c in self._store:
            if c.file_id != file_id:
                continue
            if include_embeddings:
                out.append(c)
            else:
                # Drop embedding to mirror real connector contract.
                out.append(
                    TextChunk(
                        file_id=c.file_id,
                        content=c.content,
                        metadata=c.metadata,
                        embedding=None,
                    )
                )
        return out

    async def upsert_chunks(self, chunks: list[TextChunk]) -> None:
        # Embed any chunk that doesn't already have an embedding.
        to_embed = [c for c in chunks if c.embedding is None]
        if to_embed:
            vectors = self._embedder.embed_documents([c.content for c in to_embed])
            embedded = [
                TextChunk(
                    file_id=c.file_id,
                    content=c.content,
                    metadata=c.metadata,
                    embedding=v,
                )
                for c, v in zip(to_embed, vectors, strict=True)
            ]
        else:
            embedded = []
        already_embedded = [c for c in chunks if c.embedding is not None]
        self._store.extend(embedded)
        self._store.extend(already_embedded)

    async def list_documents(self) -> list[DocumentInfo]:
        counts: dict[str, int] = {}
        for c in self._store:
            counts[c.file_id] = counts.get(c.file_id, 0) + 1
        return [DocumentInfo(file_id=fid, chunk_count=n) for fid, n in counts.items()]

    async def delete_document(self, file_id: str) -> None:
        self._store = [c for c in self._store if c.file_id != file_id]

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(ok=True, message=f"in-memory: {len(self._store)} chunks")

    def _filter_chunks(
        self,
        filter: dict[str, Any] | None,
    ) -> list[TextChunk]:
        """Filter chunks by metadata. Supports two shapes for ``file_id``:

        - ``{"file_id": "<string>"}`` — exact match.
        - ``{"file_id": {"$in": [...]}}`` — Chroma-compatible membership; the
          agent's text_retriever_node emits this shape so the unit tests must
          exercise it (otherwise filter assertions pass vacuously).
        """
        if not filter:
            return self._store
        spec = filter.get("file_id")
        if spec is None:
            return self._store
        if isinstance(spec, dict) and "$in" in spec:
            in_list = _coerce_in_list(spec["$in"])
            if in_list is None:
                return self._store
            wanted = {str(v) for v in in_list}
            return [c for c in self._store if c.file_id in wanted]
        return [c for c in self._store if c.file_id == spec]


def _coerce_in_list(raw: Any) -> list[Any] | None:
    """Narrow a Mongo-style ``$in`` value to a concrete list. Returns None
    when the value is not a list/tuple/set so the caller can fall back."""
    if isinstance(raw, (list, tuple, set)):
        out: list[Any] = []
        for v in raw:  # pyright: ignore[reportUnknownVariableType]
            out.append(v)
        return out
    return None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
