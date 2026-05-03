"""Embeddings provider base — wraps a langchain Embeddings factory."""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.embeddings import Embeddings

from hara.config.settings import ProviderSettings


class EmbeddingsProvider(ABC):
    @classmethod
    @abstractmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> Embeddings: ...

    @property
    @abstractmethod
    def vector_size(self) -> int:
        """Output dimension; used to validate consistency with vector store."""
