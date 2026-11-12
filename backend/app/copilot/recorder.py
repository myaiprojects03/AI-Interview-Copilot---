"""Writes interview history in the background, so database latency never slows the live session.

Writes are applied in order by one task. A failed write is logged and skipped: losing one
history line is better than interrupting someone's interview.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.history.repo import HistoryRepo

log = logging.getLogger(__name__)


class SessionRecorder:
    def __init__(self, history: HistoryRepo, session_id: str, connection_id: str | None = None,
                 first_seq: int = 0):
        self.history = history
        self.session_id = session_id
        self.connection_id = connection_id
        self._queue: asyncio.Queue[Callable[[], Awaitable[Any]] | None] = asyncio.Queue()
        self._task = asyncio.create_task(self._run())
        self._seq = first_seq  # a resumed session continues the transcript numbering

    async def _run(self) -> None:
        while True:
            op = await self._queue.get()
            if op is None:
                return
            try:
                await op()
            except Exception:
                log.exception("history write failed for session %s", self.session_id)

    def line(self, speaker: str, text: str) -> None:
        seq = self._seq
        self._seq += 1
        self._queue.put_nowait(lambda: self.history.add_line(self.session_id, seq, speaker, text))

    def answer(self, question_id: str, question: str, category: str, answer: str,
               unverified: list[str], timings: dict) -> None:
        self._queue.put_nowait(lambda: self.history.save_answer(
            self.session_id, question_id, question, category, answer, unverified, timings))

    def forget_answer(self, question_id: str) -> None:
        self._queue.put_nowait(lambda: self.history.delete_answer(self.session_id, question_id))

    def seconds(self, seconds: int) -> None:
        if seconds > 0:
            self._queue.put_nowait(lambda: self.history.add_seconds(self.session_id, seconds))

    async def close(self, wait_s: float = 5.0) -> None:
        self._queue.put_nowait(lambda: self.history.end_session(self.session_id, self.connection_id))
        self._queue.put_nowait(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._task), wait_s)
        except TimeoutError:
            log.warning("history writes for session %s did not finish in %.0f s", self.session_id, wait_s)
