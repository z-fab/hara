"""Tests for provider registry."""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from hara.config.settings import ProviderSettings
from hara.providers import (
    list_llm_providers,
    register_embeddings_provider,
    register_llm_provider,
    resolve_embeddings_provider_class,
    resolve_llm_provider_class,
)
from hara.providers.embeddings.base import EmbeddingsProvider
from hara.providers.llm.base import LLMProvider


class _FakeLLM(LLMProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> BaseChatModel:
        raise NotImplementedError

    @property
    def supports_structured_output(self) -> bool:
        return True


class _FakeEmb(EmbeddingsProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> Embeddings:
        raise NotImplementedError

    @property
    def vector_size(self) -> int:
        return 4


def test_register_and_resolve_llm() -> None:
    register_llm_provider("fakellm", _FakeLLM)
    assert resolve_llm_provider_class("fakellm") is _FakeLLM


def test_register_and_resolve_embeddings() -> None:
    register_embeddings_provider("fakeemb", _FakeEmb)
    assert resolve_embeddings_provider_class("fakeemb") is _FakeEmb


def test_resolve_unknown_llm_raises() -> None:
    with pytest.raises(KeyError):
        resolve_llm_provider_class("nope_zzz")


def test_list_includes_registered() -> None:
    register_llm_provider("xx", _FakeLLM)
    assert "xx" in list_llm_providers()
