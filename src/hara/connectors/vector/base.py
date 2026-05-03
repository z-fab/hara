"""Vector connector contract - abstract base + types.

All vector connectors implement this protocol. The agent's Text Retriever
calls `similarity_search`. The Semantic Map service calls `get_chunks`
+ `similarity_search_by_vector` to build document descriptions from
centroide-representative chunks. The Ingest service uses `upsert_chunks`.

To add a new connector type (Pinecone, Qdrant, Weaviate, etc.), subclass
VectorConnector, implement all abstract methods, and register via
entry-point or programmatically. See docs/extending/connectors.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from langchain_core.embeddings import Embeddings

from hara.config.schemas import VectorConnectorConfigUnion
from hara.connectors.sql.base import ConnectorHealth


@dataclass(frozen=True)
class TextChunk:
    file_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    embedding: list[float] | None = None


@dataclass(frozen=True)
class DocumentInfo:
    file_id: str
    chunk_count: int


class VectorConnector(ABC):
    """Abstract vector store connector. Async-first."""

    @classmethod
    @abstractmethod
    def from_config(
        cls,
        config: VectorConnectorConfigUnion,
        embedder: Embeddings,
    ) -> VectorConnector:
        """Construct a connector. Embedder is injected (not created internally)."""

    @abstractmethod
    async def similarity_search(
        self,
        query: str,
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        """Convenience: embed `query` and call `similarity_search_by_vector`."""

    @abstractmethod
    async def similarity_search_by_vector(
        self,
        vector: list[float],
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        """Search by raw vector. Used by semantic-map (centroide)."""

    @abstractmethod
    async def get_chunks(
        self,
        file_id: str,
        *,
        include_embeddings: bool = False,
    ) -> list[TextChunk]:
        """Return all chunks for a document.

        `include_embeddings=True` is expensive; only used by semantic-map.
        """

    @abstractmethod
    async def upsert_chunks(self, chunks: list[TextChunk]) -> None:
        """Index a batch of chunks."""

    @abstractmethod
    async def list_documents(self) -> list[DocumentInfo]:
        """List all indexed documents."""

    @abstractmethod
    async def delete_document(self, file_id: str) -> None:
        """Remove a document and all its chunks."""

    @abstractmethod
    async def health_check(self) -> ConnectorHealth: ...
