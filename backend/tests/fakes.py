import asyncio
import hashlib
import math
import re as _re
from collections.abc import AsyncIterator

from app.providers.base import (
    ChatMessage,
    EmbeddingProvider,
    LLMProvider,
    ModelTier,
    RetrievedChunk,
    Speaker,
    STTProvider,
    STTStream,
    TranscriptEvent,
    VectorStore,
)


class FakeStream(STTStream):
    """After receiving audio, emits the scripted (text, is_final) events, then waits until closed."""

    def __init__(self, speaker: Speaker, script: list[tuple[str, bool]]):
        self.speaker = speaker
        self.script = script
        self.bytes = 0
        self.got_audio = asyncio.Event()
        self.closed = asyncio.Event()

    async def send_audio(self, chunk: bytes) -> None:
        self.bytes += len(chunk)
        self.got_audio.set()

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        # Like a real stream, end when closed even if no audio ever arrived.
        audio, closed = asyncio.ensure_future(self.got_audio.wait()), asyncio.ensure_future(self.closed.wait())
        await asyncio.wait({audio, closed}, return_when=asyncio.FIRST_COMPLETED)
        audio.cancel()
        closed.cancel()
        if self.closed.is_set():
            return
        for text, final in self.script:
            yield TranscriptEvent(self.speaker, text, is_final=final, speech_final=final)
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()


class FakeSTT(STTProvider):
    def __init__(self, scripts: dict[str, list[tuple[str, bool]]] | None = None):
        self.scripts = scripts or {}
        self.streams: dict[str, FakeStream] = {}

    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream:
        s = FakeStream(speaker, self.scripts.get(speaker, []))
        self.streams[speaker] = s
        return s


class FakeLLM(LLMProvider):
    """Detects a question when the latest turn contains '?'; answers with fixed tokens."""

    def __init__(self, tokens: list[str] | None = None, token_delay: float = 0.0):
        self.tokens = tokens or ["**Answer**", " line"]
        self.token_delay = token_delay
        self.json_calls: list[list[ChatMessage]] = []
        self.chat_calls: list[list[ChatMessage]] = []
        self.closed = False

    async def complete_json(self, messages: list[ChatMessage], tier: ModelTier = "fast",
                            max_tokens: int = 200) -> dict:
        self.json_calls.append(messages)
        latest = messages[-1].content.split("Latest interviewer turn:\n", 1)[-1]
        if "?" in latest:
            question = latest.split("?")[0].strip() + "?"
            return {"is_question": True, "question": question, "category": "behavioral"}
        return {"is_question": False, "question": "", "category": "other"}

    async def stream_chat(self, messages: list[ChatMessage], tier: ModelTier = "answer",
                          max_tokens: int = 600) -> AsyncIterator[str]:
        self.chat_calls.append(messages)
        for t in self.tokens:
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            yield t

    async def aclose(self) -> None:
        self.closed = True


# ---------- retrieval fakes ----------


class FakeEmbedder(EmbeddingProvider):
    """Deterministic bag-of-words vectors: texts sharing words are similar, so ranking is meaningful."""

    DIM = 1024

    @property
    def dimension(self) -> int:
        return self.DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.DIM
        for w in _re.findall(r"[a-z]{3,}", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.DIM] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class MemoryVectorStore(VectorStore):
    def __init__(self, delay: float = 0.0):
        self.data: dict[str, dict[str, tuple[list[float], dict]]] = {}
        self.delay = delay
        self.queries: list[dict] = []

    async def upsert(self, user_id, ids, vectors, metadata):
        ns = self.data.setdefault(user_id, {})
        for i, v, m in zip(ids, vectors, metadata, strict=True):
            ns[i] = (v, m)

    async def query(self, user_id, vector, top_k=5, doc_ids=None):
        self.queries.append({"user_id": user_id, "doc_ids": doc_ids})
        if self.delay:
            await asyncio.sleep(self.delay)
        scored = []
        for _, (v, m) in self.data.get(user_id, {}).items():
            if doc_ids and m.get("doc_id") not in doc_ids:
                continue
            scored.append(RetrievedChunk(m["text"], sum(a * b for a, b in zip(v, vector, strict=True)), m))
        return sorted(scored, key=lambda c: c.score, reverse=True)[:top_k]

    async def delete(self, user_id, ids):
        for i in ids:
            self.data.get(user_id, {}).pop(i, None)

    async def exists(self, user_id, vector_id):
        return vector_id in self.data.get(user_id, {})
