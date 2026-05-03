"""LLM provider base — wraps a langchain BaseChatModel factory."""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.language_models import BaseChatModel

from hara.config.settings import ProviderSettings


class LLMProvider(ABC):
    """Stateless factory for langchain BaseChatModel instances.

    Subclasses implement `from_config()` to build a configured chat model.
    Provider name is the key in [providers.<name>] in hara.toml.
    """

    @classmethod
    @abstractmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> BaseChatModel: ...

    @property
    @abstractmethod
    def supports_structured_output(self) -> bool:
        """If False, callers must fall back to manual JSON parsing."""
