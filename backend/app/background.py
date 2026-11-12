"""Fire-and-forget background work that logs failures instead of losing them."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from fastapi import FastAPI

log = logging.getLogger(__name__)


def spawn(app: FastAPI, coro: Coroutine[Any, Any, Any], label: str) -> asyncio.Task:
    tasks: set[asyncio.Task] = getattr(app.state, "background", None) or set()
    app.state.background = tasks

    async def run():
        try:
            await coro
        except Exception:
            log.exception("background task failed: %s", label)

    task = asyncio.create_task(run())
    tasks.add(task)  # keep a reference so the task is not garbage-collected mid-run
    task.add_done_callback(tasks.discard)
    return task
