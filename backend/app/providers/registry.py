"""Builds the configured provider instances. Each concrete provider registers itself under a name;
STT_PROVIDER, LLM_PROVIDER, EMBEDDING_PROVIDER and VECTOR_PROVIDER in .env choose which is used."""
from __future__ import annotations

from collections.abc import Callable

from app.config import Settings
from app.providers.base import EmbeddingProvider, LLMProvider, STTProvider, VectorStore

_STT: dict[str, Callable[[Settings], STTProvider]] = {}
_LLM: dict[str, Callable[[Settings], LLMProvider]] = {}
_EMBED: dict[str, Callable[[Settings], EmbeddingProvider]] = {}
_VECTOR: dict[str, Callable[[Settings], VectorStore]] = {}


def register_stt(name: str):
    def deco(factory):
        _STT[name] = factory
        return factory
    return deco


def register_llm(name: str):
    def deco(factory):
        _LLM[name] = factory
        return factory
    return deco


def register_embedding(name: str):
    def deco(factory):
        _EMBED[name] = factory
        return factory
    return deco


def register_vector(name: str):
    def deco(factory):
        _VECTOR[name] = factory
        return factory
    return deco


def _build(registry: dict, name: str, kind: str, settings: Settings):
    if name not in registry:
        raise RuntimeError(f"{kind} provider '{name}' is not registered. Available: {sorted(registry)}")
    return registry[name](settings)


def get_stt(settings: Settings) -> STTProvider:
    return _build(_STT, settings.stt_provider, "STT", settings)


def get_llm(settings: Settings) -> LLMProvider:
    return _build(_LLM, settings.llm_provider, "LLM", settings)


def get_embedder(settings: Settings) -> EmbeddingProvider:
    return _build(_EMBED, settings.embedding_provider, "Embedding", settings)


def get_vector_store(settings: Settings) -> VectorStore:
    return _build(_VECTOR, settings.vector_provider, "Vector", settings)
