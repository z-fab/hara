"""Text Retriever node — vector similarity search per text subquery.

No LLM call. The embedding is computed by the connector internally
(`similarity_search` does its own embed via the injected Embeddings).
"""

from __future__ import annotations

from typing import Any

from hara.agent.state import AgentState
from hara.agent.tracking import measure_node_latency
from hara.connectors.vector.base import VectorConnector


async def text_retriever_node(
    state: AgentState,
    *,
    connector: VectorConnector,
    k: int,
) -> dict[str, Any]:
    text_subs = [sq for sq in state.get("subqueries", []) if sq.type == "text"]
    results: list[dict[str, Any]] = []

    with measure_node_latency() as timer:
        for sq in text_subs:
            filt: dict[str, Any] | None = None
            if sq.sources:
                # Chroma uses {"file_id": {"$in": [...]}}; memory connector accepts the same.
                filt = {"file_id": {"$in": list(sq.sources)}}
            chunks = await connector.similarity_search(sq.question, k=k, filter=filt)
            results.append(
                {
                    "task_query": sq.question,
                    "chunks": [
                        {
                            "file_id": c.file_id,
                            "content": c.content,
                            "metadata": dict(c.metadata),
                        }
                        for c in chunks
                    ],
                }
            )

    return {
        "text_results": results,
        "latency_per_node": {"text_retriever": timer.elapsed},
    }
