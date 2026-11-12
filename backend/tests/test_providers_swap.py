"""Swappable providers: Deepgram speech-to-text, any OpenAI-compatible LLM, and switching by config."""
import asyncio
import json
import threading
from http import HTTPStatus

import httpx
import pytest
from fastapi.testclient import TestClient
from websockets.asyncio.server import serve

from app.config import Settings
from app.copilot import session as copilot_session
from app.main import app
from app.providers import deepgram_stt
from app.providers.base import ChatMessage, LLMError
from app.providers.deepgram_stt import DeepgramProvider
from app.providers.groq_llm import GroqProvider
from app.providers.openai_compat_llm import OpenAICompatibleLLM, parse_json_object
from app.providers.registry import get_llm, get_stt
from tests.fakes import FakeLLM
from tests.test_history import _drain_until, _end


def results(text, is_final=False, speech_final=False):
    return json.dumps({"type": "Results", "is_final": is_final, "speech_final": speech_final,
                       "channel": {"alternatives": [{"transcript": text}]}})


SCRIPT = [
    results("So could"),
    results("So could you", is_final=True),
    results("tell me"),
    results("tell me about yourself?", is_final=True, speech_final=True),
    results("Next part", is_final=True),
    json.dumps({"type": "UtteranceEnd", "channel": [0], "last_word_end": 3.2}),
]


class FakeDeepgram:
    """A local WebSocket server that behaves like Deepgram's /v1/listen."""

    def __init__(self, script=SCRIPT, reject_status: HTTPStatus | None = None, close_code: int = 1000):
        self.script = script
        self.reject_status = reject_status
        self.close_code = close_code
        self.paths: list[str] = []
        self.auth: list[str] = []
        self.texts: list[dict] = []
        self.audio_bytes = 0

    def process_request(self, connection, request):
        self.paths.append(request.path)
        self.auth.append(request.headers.get("Authorization", ""))
        if self.reject_status:
            return connection.respond(self.reject_status, "rejected\n")
        return None

    async def handler(self, ws):
        sent_script = False
        async for msg in ws:
            if isinstance(msg, bytes):
                self.audio_bytes += len(msg)
                if not sent_script:
                    sent_script = True
                    for m in self.script:
                        await ws.send(m)
                continue
            data = json.loads(msg)
            self.texts.append(data)
            if data["type"] == "CloseStream":
                # Deepgram flushes what it has, then closes.
                await ws.send(results("last words", is_final=True))
                await ws.close(self.close_code, "bye")
                return


@pytest.fixture
def deepgram_server(monkeypatch):
    """Runs a FakeDeepgram in its own thread (so the app's test client can use it too)."""
    started = threading.Event()
    state: dict = {}

    def run(fake: FakeDeepgram):
        async def main():
            stop = asyncio.Event()
            state["stop"], state["loop"] = stop, asyncio.get_running_loop()
            async with serve(fake.handler, "127.0.0.1", 0, process_request=fake.process_request) as server:
                port = server.sockets[0].getsockname()[1]
                monkeypatch.setattr(deepgram_stt, "STREAMING_URL", f"ws://127.0.0.1:{port}/v1/listen")
                started.set()
                await stop.wait()
        asyncio.run(main())

    def start(fake: FakeDeepgram) -> FakeDeepgram:
        state["thread"] = threading.Thread(target=run, args=(fake,), daemon=True)
        state["thread"].start()
        assert started.wait(5)
        return fake

    yield start
    if "loop" in state:
        state["loop"].call_soon_threadsafe(state["stop"].set)
        state["thread"].join(5)


def _deepgram() -> DeepgramProvider:
    return DeepgramProvider(Settings(_env_file=None, deepgram_api_key="dg-key"))


# ---------- Deepgram ----------
def test_deepgram_joins_pieces_into_one_final_line_per_turn(deepgram_server):
    fake = deepgram_server(FakeDeepgram())

    async def run():
        stream = await _deepgram().open_stream("interviewer", sample_rate=16000)
        await stream.send_audio(b"\x00" * 3200)
        events = []

        async def collect():
            async for ev in stream.events():
                events.append(ev)
                if ev.is_final and ev.text == "Next part":
                    await stream.close()

        await asyncio.wait_for(collect(), 5)
        return events

    events = asyncio.run(run())
    assert [(e.text, e.is_final) for e in events] == [
        ("So could", False),
        ("So could you", False),
        ("So could you tell me", False),
        ("So could you tell me about yourself?", True),   # speech_final ends the turn
        ("Next part", False),
        ("Next part", True),                              # UtteranceEnd ends it after a pause
        ("last words", False),
        ("last words", True),                             # unfinished turn flushed on close
    ]
    assert all(e.speaker == "interviewer" for e in events)
    assert fake.auth == ["Token dg-key"]
    path = fake.paths[0]
    for param in ("model=nova-3", "encoding=linear16", "sample_rate=16000", "channels=1", "interim_results=true",
                  "endpointing=300", "utterance_end_ms=1000"):
        assert param in path, param
    assert {"type": "CloseStream"} in fake.texts


def test_deepgram_keeps_a_silent_stream_open(deepgram_server, monkeypatch):
    """Deepgram closes a stream with no audio for 10 s; a stream with no mic sends KeepAlive."""
    monkeypatch.setattr(deepgram_stt, "KEEPALIVE_S", 0.2)
    fake = deepgram_server(FakeDeepgram(script=[]))

    async def run():
        stream = await _deepgram().open_stream("candidate")
        await asyncio.sleep(0.7)
        await stream.close()

    asyncio.run(run())
    assert {"type": "KeepAlive"} in fake.texts


def test_deepgram_bad_key_and_abnormal_close_are_reported(deepgram_server):
    deepgram_server(FakeDeepgram(reject_status=HTTPStatus.UNAUTHORIZED))
    with pytest.raises(RuntimeError, match="DEEPGRAM_API_KEY"):
        asyncio.run(_deepgram().open_stream("interviewer"))


def test_deepgram_abnormal_close_raises(deepgram_server):
    deepgram_server(FakeDeepgram(script=[results("hi", is_final=True)], close_code=1011))

    async def run():
        stream = await _deepgram().open_stream("interviewer")
        await stream.send_audio(b"\x00" * 320)
        await stream._ws.send(json.dumps({"type": "CloseStream"}))  # server closes with 1011
        return [e async for e in stream.events()]

    with pytest.raises(RuntimeError, match="1011"):
        asyncio.run(run())


def test_full_interview_runs_on_deepgram(deepgram_server, monkeypatch, memory_repo):
    """The rest of the app is unchanged: Deepgram's turn becomes a question and a streamed answer."""
    deepgram_server(FakeDeepgram())
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: _deepgram())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "sample_rate": 16000})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        msgs = _drain_until(ws, lambda m: m["type"] == "answer_done")
        finals = [m["text"] for m in msgs if m["type"] == "transcript" and m["is_final"]]
        assert finals[0] == "So could you tell me about yourself?"
        assert any(m["type"] == "question" and m["text"].endswith("about yourself?") for m in msgs)
        _end(ws)  # waits until the server has closed both Deepgram streams


# ---------- OpenAI-compatible LLM ----------
def _llm(handler, **kw) -> tuple[OpenAICompatibleLLM, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    options = {"service": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "api_key": "or-key",
               "key_setting": "LLM_API_KEY", "answer_model": "big", "fast_model": "small", **kw}
    p = OpenAICompatibleLLM(**options)
    p._client = httpx.AsyncClient(base_url=options["base_url"], headers=p._client.headers,
                                  transport=httpx.MockTransport(record))
    return p, seen


def test_openai_compatible_streams_from_any_base_url():
    sse = ("data: " + json.dumps({"choices": [{"delta": {"content": "Hel"}}]}) + "\n\n"
           "data:" + json.dumps({"choices": [{"delta": {"content": "lo"}}]}) + "\n\n"   # no space after "data:"
           ": keep-alive comment\n\n"
           "data: [DONE]\n\n")
    p, seen = _llm(lambda r: httpx.Response(200, text=sse))

    async def run():
        return [t async for t in p.stream_chat([ChatMessage("user", "hi")], max_tokens=300)]

    assert asyncio.run(run()) == ["Hel", "lo"]
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer or-key"
    assert body["model"] == "big" and body["max_tokens"] == 300 and "reasoning_effort" not in body


def test_openai_compatible_json_without_json_mode_and_without_key():
    reply = {"choices": [{"message": {"content": 'Sure:\n```json\n{"is_question": true}\n```'}}]}
    p, seen = _llm(lambda r: httpx.Response(200, json=reply), api_key="", json_mode=False,
                   base_url="http://localhost:11434/v1", service="Ollama")
    assert asyncio.run(p.complete_json([ChatMessage("user", "x")])) == {"is_question": True}
    body = json.loads(seen[0].content)
    assert body["model"] == "small" and "response_format" not in body
    assert "authorization" not in seen[0].headers  # a local Ollama needs no key


def test_openai_compatible_errors_name_the_service():
    p, _ = _llm(lambda r: httpx.Response(401, json={"error": {"message": "no"}}))
    with pytest.raises(LLMError, match="OpenRouter rejected the API key.*LLM_API_KEY"):
        asyncio.run(p.complete_json([ChatMessage("user", "x")]))
    with pytest.raises(LLMError, match="invalid JSON"):
        parse_json_object("no json here", "OpenRouter")
    with pytest.raises(LLMError, match="LLM_BASE_URL"):
        OpenAICompatibleLLM(service="x", base_url="", api_key="", key_setting="LLM_API_KEY",
                            answer_model="m", fast_model="m")


# ---------- switching by configuration ----------
def test_providers_are_chosen_by_config_only():
    assert type(get_stt(Settings(_env_file=None, assemblyai_api_key="k"))).__name__ == "AssemblyAIProvider"
    assert isinstance(get_stt(Settings(_env_file=None, stt_provider="deepgram", deepgram_api_key="k")),
                      DeepgramProvider)
    assert isinstance(get_llm(Settings(_env_file=None, groq_api_key="k")), GroqProvider)

    async def build_compatible():
        llm = get_llm(Settings(_env_file=None, llm_provider="openai_compatible",
                               llm_base_url="http://localhost:11434/v1", llm_answer_model="llama3.2"))
        try:
            return type(llm), llm._models
        finally:
            await llm.aclose()

    kind, models = asyncio.run(build_compatible())
    assert kind is OpenAICompatibleLLM and models == {"answer": "llama3.2", "fast": "llama3.2"}
    with pytest.raises(RuntimeError, match="Available: .*deepgram"):
        get_stt(Settings(_env_file=None, stt_provider="nope"))
