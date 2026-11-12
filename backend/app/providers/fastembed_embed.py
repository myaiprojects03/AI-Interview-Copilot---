"""Local embeddings with fastembed (ONNX, CPU). Free: no API calls, no per-request cost.

The model (BAAI/bge-small-en-v1.5, 384 dimensions, about 70 MB) downloads once on first use
and is then cached on disk. It is loaded once per server process and shared by all requests.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from app.config import Settings
from app.providers.base import EmbeddingProvider
from app.providers.registry import register_embedding

log = logging.getLogger(__name__)

_DIMENSIONS = {"BAAI/bge-small-en-v1.5": 384, "BAAI/bge-base-en-v1.5": 768}
_models: dict[str, object] = {}
_lock = threading.Lock()


def _load(model_name: str):
    with _lock:
        if model_name not in _models:
            from fastembed import TextEmbedding  # imported lazily: heavy import

            log.info("loading embedding model %s (first run downloads it)", model_name)
            _models[model_name] = TextEmbedding(model_name)
            log.info("embedding model ready")
        return _models[model_name]


class FastEmbedProvider(EmbeddingProvider):
    def __init__(self, settings: Settings):
        self._name = settings.local_embedding_model
        if self._name not in _DIMENSIONS:
            raise RuntimeError(f"Unsupported LOCAL_EMBEDDING_MODEL {self._name}. Use one of {sorted(_DIMENSIONS)}")

    @property
    def dimension(self) -> int:
        return _DIMENSIONS[self._name]

    async def warm_up(self) -> None:
        await asyncio.to_thread(_load, self._name)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def run():
            return [v.tolist() for v in _load(self._name).passage_embed(texts)]
        return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> list[float]:
        # BGE models expect an instruction prefix on queries; query_embed adds it.
        def run():
            return next(iter(_load(self._name).query_embed(text))).tolist()
        return await asyncio.to_thread(run)


@register_embedding("local")
def _factory(settings: Settings) -> EmbeddingProvider:
    return FastEmbedProvider(settings)
