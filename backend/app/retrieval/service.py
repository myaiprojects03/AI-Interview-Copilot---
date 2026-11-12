"""Index CVs / JDs and retrieve the most relevant parts for a question."""
from __future__ import annotations

import logging

from app.documents.repo import Document
from app.providers.base import EmbeddingProvider, RetrievedChunk, VectorStore
from app.retrieval.chunking import chunk_text

log = logging.getLogger(__name__)


def chunk_ids(doc: Document) -> list[str]:
    # Deterministic ids: re-indexing overwrites, deleting needs no extra bookkeeping.
    return [f"{doc.id}#{i}" for i in range(len(chunk_text(doc.text)))]


class RetrievalService:
    def __init__(self, embedder: EmbeddingProvider, store: VectorStore):
        self.embedder = embedder
        self.store = store

    async def index_document(self, doc: Document) -> int:
        chunks = chunk_text(doc.text)
        if not chunks:
            return 0
        vectors = await self.embedder.embed(chunks)
        ids = [f"{doc.id}#{i}" for i in range(len(chunks))]
        meta = [{"doc_id": doc.id, "kind": doc.kind, "chunk": i, "text": c} for i, c in enumerate(chunks)]
        await self.store.upsert(doc.user_id, ids, vectors, meta)
        log.info("indexed %s %s: %d chunks", doc.kind, doc.id, len(chunks))
        return len(chunks)

    async def ensure_indexed(self, doc: Document) -> None:
        """Index a document if its vectors are missing (e.g. Pinecone was down at upload time)."""
        if not await self.store.exists(doc.user_id, f"{doc.id}#0"):
            await self.index_document(doc)

    async def delete_document(self, doc: Document) -> None:
        await self.store.delete(doc.user_id, chunk_ids(doc))

    async def retrieve(self, user_id: str, query: str, doc_ids: list[str], top_k: int = 4) -> list[RetrievedChunk]:
        if not doc_ids or not query.strip():
            return []
        vector = await self.embedder.embed_query(query)
        return await self.store.query(user_id, vector, top_k=top_k, doc_ids=doc_ids)

    async def aclose(self) -> None:
        await self.store.aclose()
