"""OpenAI LLM provider via langchain-openai."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from hara.config.settings import ProviderSettings
from hara.providers.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> BaseChatModel:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in environment")
        kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        return ChatOpenAI(**kwargs)

    @property
    def supports_structured_output(self) -> bool:
        return True
