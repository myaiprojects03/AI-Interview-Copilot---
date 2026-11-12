import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.providers.base import ChatMessage, LLMError
from app.providers.groq_llm import GroqProvider


def _provider(handler) -> tuple[GroqProvider, list[dict]]:
    seen: list[dict] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return handler(request)

    p = GroqProvider(Settings(groq_api_key="k"))
    p._client = httpx.AsyncClient(base_url="https://api.groq.com/openai/v1",
                                  transport=httpx.MockTransport(record))
    return p, seen


def test_defaults_use_current_free_tier_models():
    s = Settings(_env_file=None)
    assert s.groq_answer_model == "openai/gpt-oss-120b"
    assert s.groq_fast_model == "openai/gpt-oss-20b"
    assert s.groq_reasoning_effort == "low"


def test_stream_sends_reasoning_effort_and_ignores_reasoning_deltas():
    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in [
        {"choices": [{"delta": {"reasoning": "thinking..."}}]},
        {"choices": [{"delta": {"content": "**Hello"}}]},
        {"choices": [{"delta": {"content": " world**"}}]},
        {"choices": []},
    ]) + "data: [DONE]\n\n"
    p, seen = _provider(lambda r: httpx.Response(200, text=sse))

    async def run():
        return [t async for t in p.stream_chat([ChatMessage("user", "hi")], tier="answer", max_tokens=600)]

    assert asyncio.run(run()) == ["**Hello", " world**"]
    body = seen[0]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 600 + 1024
    assert body["stream"] is True


def test_json_uses_fast_model():
    reply = {"choices": [{"message": {"content": '{"is_question": true}'}}]}
    p, seen = _provider(lambda r: httpx.Response(200, json=reply))
    assert asyncio.run(p.complete_json([ChatMessage("user", "x")])) == {"is_question": True}
    assert seen[0]["model"] == "openai/gpt-oss-20b"
    assert seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("status,needle", [(429, "rate limit"), (401, "invalid"),
                                           (404, "does not exist")])
def test_errors_are_readable(status, needle):
    body = {"error": {"message": "The model `x` does not exist"}}
    p, _ = _provider(lambda r: httpx.Response(status, json=body))
    with pytest.raises(LLMError, match=needle):
        asyncio.run(p.complete_json([ChatMessage("user", "x")]))
