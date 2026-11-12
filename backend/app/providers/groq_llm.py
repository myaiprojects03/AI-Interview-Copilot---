"""Groq LLM provider (free tier): the OpenAI-compatible provider with Groq's address and models."""
from __future__ import annotations

from app.config import Settings
from app.providers.base import LLMError, LLMProvider
from app.providers.openai_compat_llm import OpenAICompatibleLLM
from app.providers.registry import register_llm

BASE_URL = "https://api.groq.com/openai/v1"


class GroqProvider(OpenAICompatibleLLM):
    def __init__(self, settings: Settings):
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not set in .env")
        super().__init__(service="Groq", base_url=BASE_URL, api_key=settings.groq_api_key,
                         key_setting="GROQ_API_KEY", answer_model=settings.groq_answer_model,
                         fast_model=settings.groq_fast_model, reasoning_effort=settings.groq_reasoning_effort)


@register_llm("groq")
def _factory(settings: Settings) -> LLMProvider:
    return GroqProvider(settings)
