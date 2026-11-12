"""Provider interfaces. Concrete providers (AssemblyAI, Deepgram, Groq, any OpenAI-compatible API,
fastembed, Pinecone) implement these,
so a provider can be swapped by changing config, not application code."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

Speaker = Literal["interviewer", "candidate"]


@dataclass
class TranscriptEvent:
    speaker: Speaker
    text: str
    is_final: bool  # False = partial (interim) result
    speech_final: bool = False  # True = provider detected end of utterance
    start: float = 0.0
    end: float = 0.0


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass
class RetrievedChunk:
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class STTStream(ABC):
    """One live transcription stream for one audio channel."""

    @abstractmethod
    async def send_audio(self, chunk: bytes) -> None: ...

    @abstractmethod
    def events(self) -> AsyncIterator[TranscriptEvent]: ...

    @abstractmethod
    async def close(self) -> None: ...


class STTProvider(ABC):
    @abstractmethod
    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream: ...


ModelTier = Literal["answer", "fast"]  # answer = best quality, fast = low-latency detection


class LLMError(RuntimeError):
    """Provider failure with a message that is safe to show the user."""


class LLMProvider(ABC):
    @abstractmethod
    def stream_chat(self, messages: list[ChatMessage], tier: ModelTier = "answer",
                    max_tokens: int = 600) -> AsyncIterator[str]:
        """Yield response tokens as they arrive."""

    @abstractmethod
    async def complete_json(self, messages: list[ChatMessage], tier: ModelTier = "fast",
                            max_tokens: int = 200) -> dict:
        """Return a single JSON object response."""

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        """Release network resources."""


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed document passages (CV/JD chunks)."""

    async def embed_query(self, text: str) -> list[float]:
        """Embed a search query. Override when the model uses a query instruction."""
        return (await self.embed([text]))[0]

    async def warm_up(self) -> None:  # noqa: B027  (optional hook)
        """Load the model ahead of the first request (may download it)."""


class VectorStore(ABC):
    """Per-user vector storage. user_id is used as the namespace, so users never share results."""

    @abstractmethod
    async def upsert(self, user_id: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict]) -> None: ...

    @abstractmethod
    async def query(self, user_id: str, vector: list[float], top_k: int = 5,
                    doc_ids: list[str] | None = None) -> list[RetrievedChunk]:
        """Nearest chunks, optionally limited to the given documents."""

    @abstractmethod
    async def delete(self, user_id: str, ids: list[str]) -> None: ...

    @abstractmethod
    async def exists(self, user_id: str, vector_id: str) -> bool: ...

    async def aclose(self) -> None:  # noqa: B027  (optional hook)
        pass
