"""Anthropic LLM provider via langchain-anthropic.

Note: Anthropic does not provide a native embeddings API. Users wanting
to use Anthropic models for the LLM stage should configure embeddings
from OpenAI or Google separately.
"""

from __future__ import annotations

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from hara.config.settings import ProviderSettings
from hara.providers.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    @classmethod
    def from_config(
        cls,
        model: str,
        settings: ProviderSettings,  # noqa: ARG003 — required by ABC
    ) -> BaseChatModel:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in environment")
        return ChatAnthropic(
            model_name=model,
            api_key=SecretStr(api_key),
            timeout=None,
            stop=None,
        )

    @property
    def supports_structured_output(self) -> bool:
        return True
