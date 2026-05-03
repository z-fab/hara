"""ChromaDB connector (persistent local).

Uses chromadb's PersistentClient. Embeddings provided externally — we set
embedding_function=None on the collection so chromadb doesn't try to embed
itself; we embed via langchain in `similarity_search` and pass vectors
directly to `query`/`add`.

Note: chromadb's typed API (Metadata/Embedding/QueryResult/GetResult) is
intentionally narrow and uses TypedDicts that don't compose well with our
internal `dict[str, Any]` shapes. We cast at the chromadb boundary to keep
the rest of HARA's strict typing intact.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, cast

import chromadb
from langchain_core.embeddings import Embeddings

from hara.config.schemas import ChromaDBVectorConfig, VectorConnectorConfigUnion
from hara.connectors.sql.base import ConnectorHealth
from hara.connectors.vector.base import DocumentInfo, TextChunk, VectorConnector

_COLLECTION_NAME = "hara_default"


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Drop empty-list values that Chroma rejects.

    Chroma's upsert validates list metadata as non-empty. Producers should
    avoid emitting empty lists at all (the chunker uses string-typed
    section now), but this guards against future regressions.
    """
    out: dict[str, Any] = {}
    for k, v in meta.items():
        # Cast keeps pyright happy: ``isinstance`` narrows to ``list``
        # without an element type, but len() then warns about Unknown.
        if isinstance(v, list) and len(cast(list[Any], v)) == 0:
            continue
        out[k] = v
    return out


class ChromaDBConnector(VectorConnector):
    def __init__(self, persist_directory: Path, embedder: Embeddings) -> None:
        persist_directory.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_directory))
        self._embedder = embedder
        # embedding_function=None: HARA embeds via the injected Embeddings.
        # Cast: chromadb's get_or_create_collection uses an embedding-function
        # protocol we deliberately bypass.
        self._collection: Any = self._client.get_or_create_collection(
            name=_COLLECTION_NAME, embedding_function=None
        )

    @classmethod
    def from_config(
        cls,
        config: VectorConnectorConfigUnion,
        embedder: Embeddings,
    ) -> ChromaDBConnector:
        if not isinstance(config, ChromaDBVectorConfig):
            raise TypeError(f"Expected ChromaDBVectorConfig, got {type(config).__name__}")
        return cls(persist_directory=config.persist_directory, embedder=embedder)

    async def similarity_search(
        self,
        query: str,
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        vec = await asyncio.to_thread(self._embedder.embed_query, query)
        return await self.similarity_search_by_vector(vec, k=k, filter=filter)

    async def similarity_search_by_vector(
        self,
        vector: list[float],
        *,
        k: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        where = self._build_where(filter)
        result = await asyncio.to_thread(
            self._collection.query,
            query_embeddings=[vector],
            n_results=k,
            where=where,
        )
        return self._chunks_from_query_result(
            cast(dict[str, Any], result), include_embeddings=False
        )

    async def get_chunks(
        self,
        file_id: str,
        *,
        include_embeddings: bool = False,
    ) -> list[TextChunk]:
        include: list[Any] = ["documents", "metadatas"]
        if include_embeddings:
            include.append("embeddings")
        result = await asyncio.to_thread(
            self._collection.get,
            where={"file_id": file_id},
            include=include,
        )
        return self._chunks_from_get_result(
            cast(dict[str, Any], result), include_embeddings=include_embeddings
        )

    async def upsert_chunks(self, chunks: list[TextChunk]) -> None:
        if not chunks:
            return
        # Embed any chunk missing an embedding.
        to_embed = [c for c in chunks if c.embedding is None]
        embeddings_map: dict[int, list[float]] = {}
        if to_embed:
            vectors = await asyncio.to_thread(
                self._embedder.embed_documents, [c.content for c in to_embed]
            )
            for c, v in zip(to_embed, vectors, strict=True):
                embeddings_map[id(c)] = v

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        embeddings: list[list[float]] = []
        for c in chunks:
            # why: Python's built-in hash() is salted per-process, so re-ingesting
            # the same content under a fresh interpreter would produce a different
            # ID and Chroma would store duplicates instead of replacing chunks.
            #
            # We also fold chunk_index into the ID: PDFs often repeat boilerplate
            # (page headers/footers) and the chunker's overlap window can produce
            # identical content across consecutive chunks. Hashing content alone
            # collapsed those into a single Chroma row while session_store
            # recorded N — counts diverged silently. chunk_index is set by
            # ``chunk_text`` in ``hara.ingest.chunking``; legacy callers that
            # don't set it default to 0 (acceptable for first-run/test paths).
            content_hash = hashlib.sha256(c.content.encode("utf-8")).hexdigest()[:16]
            chunk_index = c.metadata.get("chunk_index", 0)
            chunk_id = f"{c.file_id}_{chunk_index}_{content_hash}"
            ids.append(chunk_id)
            documents.append(c.content)
            metadatas.append(_sanitize_metadata({"file_id": c.file_id, **c.metadata}))
            emb = c.embedding if c.embedding is not None else embeddings_map[id(c)]
            embeddings.append(emb)

        await asyncio.to_thread(
            self._collection.upsert,
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    async def list_documents(self) -> list[DocumentInfo]:
        result = cast(
            dict[str, Any],
            await asyncio.to_thread(self._collection.get, include=["metadatas"]),
        )
        counts: dict[str, int] = {}
        metadatas: list[Any] = list(result.get("metadatas") or [])
        for meta in metadatas:
            meta_any: dict[str, Any] = dict(meta) if meta else {}
            fid = meta_any.get("file_id")
            if fid:
                fid_str = str(fid)
                counts[fid_str] = counts.get(fid_str, 0) + 1
        return [DocumentInfo(file_id=fid, chunk_count=n) for fid, n in counts.items()]

    async def delete_document(self, file_id: str) -> None:
        await asyncio.to_thread(self._collection.delete, where={"file_id": file_id})

    async def health_check(self) -> ConnectorHealth:
        try:
            await asyncio.to_thread(self._client.heartbeat)
            return ConnectorHealth(ok=True, message="chromadb ready")
        except Exception as e:
            return ConnectorHealth(ok=False, message=f"chromadb failed: {e}")

    @staticmethod
    def _build_where(filter: dict[str, Any] | None) -> dict[str, Any] | None:
        if not filter:
            return None
        return filter

    @staticmethod
    def _chunks_from_query_result(
        result: dict[str, Any], *, include_embeddings: bool
    ) -> list[TextChunk]:
        ids = result.get("ids", [[]])
        if not ids or not ids[0]:
            return []
        docs: list[Any] = result.get("documents", [[]])[0]
        metas: list[Any] = result.get("metadatas", [[]])[0]
        embs: list[Any] | None = (
            result.get("embeddings", [[None] * len(docs)])[0] if include_embeddings else None
        )
        chunks: list[TextChunk] = []
        for i, (doc, meta) in enumerate(zip(docs, metas, strict=True)):
            meta_dict: dict[str, Any] = dict(meta) if meta else {}
            file_id = str(meta_dict.pop("file_id", "unknown"))
            chunks.append(
                TextChunk(
                    file_id=file_id,
                    content=str(doc),
                    metadata=meta_dict,
                    embedding=list(embs[i]) if embs is not None else None,
                )
            )
        return chunks

    @staticmethod
    def _chunks_from_get_result(
        result: dict[str, Any], *, include_embeddings: bool
    ) -> list[TextChunk]:
        docs: list[Any] = list(result.get("documents") or [])
        metas: list[Any] = list(result.get("metadatas") or [])
        embs_raw = result.get("embeddings")
        embs: list[Any] | None = list(embs_raw) if embs_raw is not None else None
        chunks: list[TextChunk] = []
        for i, doc in enumerate(docs):
            meta: dict[str, Any] = dict(metas[i]) if i < len(metas) else {}
            file_id = str(meta.pop("file_id", "unknown"))
            embedding: list[float] | None = None
            if include_embeddings and embs is not None and i < len(embs):
                embedding = list(embs[i]) if embs[i] is not None else None
            chunks.append(
                TextChunk(
                    file_id=file_id,
                    content=str(doc),
                    metadata=meta,
                    embedding=embedding,
                )
            )
        return chunks
