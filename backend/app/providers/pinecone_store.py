"""Pinecone vector store over the REST API (stable version 2025-10, classic Vectors API).

- The index is created automatically on first use (serverless, aws / us-east-1 for the free plan).
- Each user's vectors live in their own namespace (namespace = user_id).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings
from app.providers.base import RetrievedChunk, VectorStore
from app.providers.registry import register_vector

log = logging.getLogger(__name__)

CONTROL_URL = "https://api.pinecone.io"
API_VERSION = "2025-10"
READY_TIMEOUT_S = 90


class PineconeError(RuntimeError):
    pass


class PineconeStore(VectorStore):
    def __init__(self, settings: Settings, dimension: int, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.pinecone_api_key:
            raise PineconeError("PINECONE_API_KEY is not set in .env")
        self._index = settings.pinecone_index
        self._cloud = settings.pinecone_cloud
        self._region = settings.pinecone_region
        self._dimension = dimension
        headers = {"Api-Key": settings.pinecone_api_key, "X-Pinecone-Api-Version": API_VERSION}
        self._http = httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(15.0, connect=5.0),
                                       transport=transport)
        self._host: str | None = None
        self._host_lock = asyncio.Lock()
        self.ready_poll_s = 2.0

    # ---------- index management ----------
    async def _describe(self) -> dict | None:
        r = await self._http.get(f"{CONTROL_URL}/indexes/{self._index}")
        if r.status_code == 404:
            return None
        self._raise(r, "describe index")
        return r.json()

    async def _host_url(self) -> str:
        if self._host:
            return self._host
        async with self._host_lock:
            if self._host:
                return self._host
            info = await self._describe()
            if info is None:
                log.info("creating Pinecone index %s (%d dims, %s/%s)", self._index, self._dimension,
                         self._cloud, self._region)
                r = await self._http.post(f"{CONTROL_URL}/indexes", json={
                    "name": self._index,
                    "dimension": self._dimension,
                    "metric": "cosine",
                    "vector_type": "dense",
                    "deletion_protection": "disabled",
                    "spec": {"serverless": {"cloud": self._cloud, "region": self._region}},
                })
                if r.status_code != 409:  # 409 = created concurrently by another process
                    self._raise(r, "create index")
                info = await self._describe()
            elif info.get("dimension") not in (None, self._dimension):
                raise PineconeError(
                    f"Pinecone index '{self._index}' has dimension {info.get('dimension')}, but the embedding "
                    f"model needs {self._dimension}. Use a different PINECONE_INDEX name."
                )
            waited = 0.0
            while not (info or {}).get("status", {}).get("ready"):
                if waited >= READY_TIMEOUT_S:
                    raise PineconeError("Pinecone index is still initialising. Try again in a minute.")
                await asyncio.sleep(self.ready_poll_s)
                waited += self.ready_poll_s
                info = await self._describe()
            assert info is not None  # the loop only exits once the index reports ready
            host = info["host"]
            self._host = host if host.startswith("http") else f"https://{host}"
            return self._host

    @staticmethod
    def _raise(r: httpx.Response, action: str) -> None:
        if r.status_code < 400:
            return
        if r.status_code in (401, 403):
            raise PineconeError("Pinecone rejected the API key. Check PINECONE_API_KEY in .env.")
        try:
            detail = r.json().get("error", {}).get("message") or r.text[:200]
        except ValueError:
            detail = r.text[:200]
        raise PineconeError(f"Pinecone {action} failed ({r.status_code}): {detail}")

    # ---------- data ----------
    async def upsert(self, user_id: str, ids: list[str], vectors: list[list[float]],
                     metadata: list[dict]) -> None:
        host = await self._host_url()
        items = [{"id": i, "values": v, "metadata": m} for i, v, m in zip(ids, vectors, metadata, strict=True)]
        for start in range(0, len(items), 100):  # Pinecone recommends batches of up to ~100
            r = await self._http.post(f"{host}/vectors/upsert",
                                      json={"vectors": items[start:start + 100], "namespace": user_id})
            self._raise(r, "upsert")

    async def query(self, user_id: str, vector: list[float], top_k: int = 5,
                    doc_ids: list[str] | None = None) -> list[RetrievedChunk]:
        host = await self._host_url()
        body: dict = {"namespace": user_id, "vector": vector, "topK": top_k, "includeMetadata": True}
        if doc_ids:
            body["filter"] = {"doc_id": {"$in": doc_ids}}
        r = await self._http.post(f"{host}/query", json=body)
        if r.status_code == 404:  # namespace does not exist yet
            return []
        self._raise(r, "query")
        return [RetrievedChunk(m.get("metadata", {}).get("text", ""), float(m.get("score", 0.0)),
                               m.get("metadata", {})) for m in r.json().get("matches", [])]

    async def delete(self, user_id: str, ids: list[str]) -> None:
        if not ids:
            return
        host = await self._host_url()
        r = await self._http.post(f"{host}/vectors/delete", json={"ids": ids, "namespace": user_id})
        if r.status_code != 404:
            self._raise(r, "delete")

    async def exists(self, user_id: str, vector_id: str) -> bool:
        host = await self._host_url()
        r = await self._http.get(f"{host}/vectors/fetch", params={"ids": vector_id, "namespace": user_id})
        if r.status_code == 404:
            return False
        self._raise(r, "fetch")
        return vector_id in (r.json().get("vectors") or {})

    async def aclose(self) -> None:
        await self._http.aclose()


@register_vector("pinecone")
def _factory(settings: Settings) -> VectorStore:
    from app.providers.registry import get_embedder
    return PineconeStore(settings, get_embedder(settings).dimension)
