"""Short-lived state shared by every server instance: sign-in rate limits and live-session tickets.

With several backend instances behind a load balancer, this state cannot live in one process's
memory (a rate limit would reset by hitting another instance, and a ticket issued by one instance
could not be checked by another). Redis holds it. In development without Redis an in-memory store
is used, which is correct for a single process only.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from fastapi import Request
from redis.asyncio import Redis

from app.config import Settings

log = logging.getLogger(__name__)

PREFIX = "copilot:"


class SharedStore(ABC):
    @abstractmethod
    async def hit(self, key: str, window_s: int) -> int:
        """Count one event in a window that starts at the first event; returns the count so far."""

    @abstractmethod
    async def count(self, key: str) -> int: ...

    @abstractmethod
    async def clear(self, key: str) -> None: ...

    @abstractmethod
    async def put(self, key: str, value: str, ttl_s: int) -> None: ...

    @abstractmethod
    async def take(self, key: str) -> str | None:
        """Read and delete in one step (single use)."""

    async def aclose(self) -> None:
        return None


class RedisStore(SharedStore):
    def __init__(self, client: Redis):
        self.client = client

    async def hit(self, key: str, window_s: int) -> int:
        k = PREFIX + key
        pipe = self.client.pipeline(transaction=True)
        pipe.incr(k)
        pipe.expire(k, window_s, nx=True)  # the window starts at the first event
        n, _ = await pipe.execute()
        return int(n)

    async def count(self, key: str) -> int:
        v = await self.client.get(PREFIX + key)
        return int(v) if v else 0

    async def clear(self, key: str) -> None:
        await self.client.delete(PREFIX + key)

    async def put(self, key: str, value: str, ttl_s: int) -> None:
        await self.client.set(PREFIX + key, value, ex=ttl_s)

    async def take(self, key: str) -> str | None:
        v = await self.client.getdel(PREFIX + key)
        return v if v is None or isinstance(v, str) else v.decode()

    async def aclose(self) -> None:
        await self.client.aclose()


class MemoryStore(SharedStore):
    """One process only: development without Redis, and tests."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def _get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] <= time.monotonic():
            del self._data[key]
            return None
        return item[0]

    async def hit(self, key: str, window_s: int) -> int:
        current = self._get(key)
        if current is None:
            self._data[key] = ("1", time.monotonic() + window_s)
            return 1
        n = int(current) + 1
        self._data[key] = (str(n), self._data[key][1])
        return n

    async def count(self, key: str) -> int:
        v = self._get(key)
        return int(v) if v else 0

    async def clear(self, key: str) -> None:
        self._data.pop(key, None)

    async def put(self, key: str, value: str, ttl_s: int) -> None:
        self._data[key] = (value, time.monotonic() + ttl_s)

    async def take(self, key: str) -> str | None:
        v = self._get(key)
        self._data.pop(key, None)
        return v


async def build_store(settings: Settings) -> SharedStore:
    """Redis when reachable. Outside development Redis is required: running several instances
    with per-process state would silently weaken rate limits and break live-session tickets."""
    if settings.redis_url:
        client = Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=3, health_check_interval=30)
        try:
            await client.ping()
            log.info("redis connected")
            return RedisStore(client)
        except Exception as exc:
            await client.aclose()
            if settings.app_env != "development":
                raise RuntimeError(f"Redis is required outside development and is not reachable: {exc}") from exc
            log.warning("redis unavailable (%s): using in-memory state (fine for one local server)", exc)
    elif settings.app_env != "development":
        raise RuntimeError("REDIS_URL must be set outside development.")
    return MemoryStore()


_fallback = MemoryStore()


def get_store(request: Request) -> SharedStore:
    """The app's store (set at startup). Unit tests that skip startup get a process-local one."""
    return getattr(request.app.state, "store", None) or _fallback


def store_of(app) -> SharedStore:
    return getattr(app.state, "store", None) or _fallback
