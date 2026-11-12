"""LLM provider for any OpenAI-compatible Chat Completions API.

One implementation serves several services by configuration only:
  - Groq (the default, see groq_llm.py)
  - OpenRouter (free models): LLM_BASE_URL=https://openrouter.ai/api/v1
  - Ollama on your own machine or server (free, local): LLM_BASE_URL=http://localhost:11434/v1
  - OpenAI, Together, Mistral, DeepSeek, ... (paid)
Select it with LLM_PROVIDER=openai_compatible and the LLM_* settings in .env.
"""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import httpx

from app.config import Settings
from app.providers.base import ChatMessage, LLMError, LLMProvider, ModelTier
from app.providers.registry import register_llm

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(content: str, service: str) -> dict:
    """Models without a JSON mode sometimes wrap the object in prose or ``` fences."""
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(content)
        try:
            value = json.loads(match.group(0)) if match else None
        except json.JSONDecodeError:
            value = None
    if not isinstance(value, dict):
        raise LLMError(f"{service} returned invalid JSON")
    return value


class OpenAICompatibleLLM(LLMProvider):
    def __init__(self, *, service: str, base_url: str, api_key: str, key_setting: str,
                 answer_model: str, fast_model: str, reasoning_effort: str = "", json_mode: bool = True,
                 timeout_s: float = 30.0):
        if not base_url:
            raise LLMError("LLM_BASE_URL is not set in .env")
        if not answer_model or not fast_model:
            raise LLMError("LLM_ANSWER_MODEL and LLM_FAST_MODEL must be set in .env")
        self.service = service
        self._key_setting = key_setting
        self._models: dict[ModelTier, str] = {"answer": answer_model, "fast": fast_model}
        self._reasoning_effort = reasoning_effort.strip()
        self._json_mode = json_mode
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}  # local Ollama needs no key
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers,
                                         timeout=httpx.Timeout(timeout_s, connect=5.0))

    def _error(self, status: int, body: bytes) -> LLMError:
        if status == 429:
            return LLMError(f"{self.service} rate limit reached. Wait a few seconds and press Regenerate.")
        if status in (401, 403):
            return LLMError(f"{self.service} rejected the API key as invalid. Check {self._key_setting} in .env.")
        try:
            detail = json.loads(body).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = body[:200].decode(errors="ignore")
        return LLMError(f"{self.service} error {status}: {detail}")

    def _unreachable(self, exc: Exception) -> LLMError:
        return LLMError(f"Could not reach {self.service}: {exc.__class__.__name__}")

    def _body(self, tier: ModelTier, messages: list[ChatMessage], max_tokens: int, **extra) -> dict:
        body = {"model": self._models[tier], "messages": [{"role": m.role, "content": m.content} for m in messages],
                **extra}
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
            # Reasoning tokens count against max_tokens, so leave room for them.
            max_tokens += 1024
        body["max_tokens"] = max_tokens
        return body

    async def stream_chat(self, messages: list[ChatMessage], tier: ModelTier = "answer",
                          max_tokens: int = 600) -> AsyncIterator[str]:
        body = self._body(tier, messages, max_tokens, temperature=0.4, stream=True)
        try:
            async with self._client.stream("POST", "/chat/completions", json=body) as r:
                if r.status_code != 200:
                    raise self._error(r.status_code, await r.aread())
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data:
                        continue
                    chunk = json.loads(data)
                    choices = chunk.get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    async def complete_json(self, messages: list[ChatMessage], tier: ModelTier = "fast",
                            max_tokens: int = 200) -> dict:
        extra: dict = {"temperature": 0}
        if self._json_mode:
            extra["response_format"] = {"type": "json_object"}
        body = self._body(tier, messages, max_tokens, **extra)
        try:
            r = await self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc
        if r.status_code != 200:
            raise self._error(r.status_code, r.content)
        content = r.json()["choices"][0]["message"].get("content") or ""
        return parse_json_object(content, self.service)

    async def aclose(self) -> None:
        await self._client.aclose()


@register_llm("openai_compatible")
def _factory(settings: Settings) -> LLMProvider:
    return OpenAICompatibleLLM(
        service=settings.llm_service_name or "The answer service",
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        key_setting="LLM_API_KEY",
        answer_model=settings.llm_answer_model,
        fast_model=settings.llm_fast_model or settings.llm_answer_model,
        reasoning_effort=settings.llm_reasoning_effort,
        json_mode=settings.llm_json_mode,
        timeout_s=settings.llm_timeout_s,
    )
