"""Deepgram live streaming speech-to-text provider (free signup credit, no card).

Select it with STT_PROVIDER=deepgram and DEEPGRAM_API_KEY in .env.
Browser -> our backend -> Deepgram. The API key stays on the server.
Docs: https://developers.deepgram.com/reference/speech-to-text/listen-streaming

Deepgram sends several final pieces per spoken turn (is_final) and marks the end of the turn
(speech_final, or an UtteranceEnd message after a pause). The rest of the app expects one final
line per turn, as AssemblyAI sends, so the pieces are joined here: partial updates show the turn
so far, and one final event carries the whole turn.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.config import Settings
from app.providers.base import Speaker, STTProvider, STTStream, TranscriptEvent
from app.providers.registry import register_stt

log = logging.getLogger(__name__)

STREAMING_URL = "wss://api.deepgram.com/v1/listen"
CONNECT_ATTEMPTS = 3
RETRY_BACKOFF_S = 0.5
# Deepgram closes a stream that gets no audio for 10 s (NET-0001), e.g. a microphone that
# sends nothing. A KeepAlive is sent whenever no audio went out for this long.
KEEPALIVE_S = 4.0
CLOSE_WAIT_S = 3.0


class DeepgramStream(STTStream):
    def __init__(self, ws: ClientConnection, speaker: Speaker):
        self._ws = ws
        self._speaker = speaker
        self._closed = False
        self._last_sent = time.monotonic()
        self._finals: list[str] = []          # final pieces of the turn in progress
        self._keepalive = asyncio.create_task(self._keep_alive())

    async def _keep_alive(self) -> None:
        while not self._closed:
            await asyncio.sleep(KEEPALIVE_S / 2)
            if not self._closed and time.monotonic() - self._last_sent >= KEEPALIVE_S:
                try:
                    await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    self._last_sent = time.monotonic()
                except ConnectionClosed:
                    return

    async def send_audio(self, chunk: bytes) -> None:
        if self._closed:
            return
        try:
            await self._ws.send(chunk)
            self._last_sent = time.monotonic()
        except ConnectionClosed:
            self._closed = True

    def _turn_text(self, current: str = "") -> str:
        return " ".join(p for p in (*self._finals, current) if p)

    def _end_turn(self) -> TranscriptEvent | None:
        text = self._turn_text()
        self._finals.clear()
        if not text:
            return None
        return TranscriptEvent(speaker=self._speaker, text=text, is_final=True, speech_final=True)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Results":
                    alternatives = (msg.get("channel") or {}).get("alternatives") or [{}]
                    text = (alternatives[0].get("transcript") or "").strip()
                    if msg.get("is_final"):
                        if text:
                            self._finals.append(text)
                        if msg.get("speech_final"):
                            if (event := self._end_turn()) is not None:
                                yield event
                        elif text:
                            yield TranscriptEvent(self._speaker, self._turn_text(), is_final=False)
                    elif text:
                        yield TranscriptEvent(self._speaker, self._turn_text(text), is_final=False)
                elif kind == "UtteranceEnd":
                    # A pause ended the turn without speech_final (e.g. background noise).
                    if (event := self._end_turn()) is not None:
                        yield event
                elif kind == "Metadata":
                    log.info("Deepgram %s stream %s: %.1fs audio", self._speaker, msg.get("request_id"),
                             msg.get("duration") or 0)
                elif kind == "Error":
                    log.warning("Deepgram %s: %s", self._speaker, msg.get("description") or msg)
        except ConnectionClosed as exc:
            if exc.rcvd and exc.rcvd.code not in (1000, 1001):
                raise RuntimeError(f"Deepgram closed the stream: {exc.rcvd.code} {exc.rcvd.reason}") from exc
        finally:
            self._closed = True
            self._keepalive.cancel()
        # Words of an unfinished turn when the stream ends (the interview was ended mid-sentence).
        if (event := self._end_turn()) is not None:
            yield event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._keepalive.cancel()
        try:
            # CloseStream makes Deepgram send its last results, then close the connection.
            await self._ws.send(json.dumps({"type": "CloseStream"}))
            await asyncio.wait_for(self._ws.wait_closed(), timeout=CLOSE_WAIT_S)
        except (TimeoutError, ConnectionClosed):
            pass
        finally:
            with contextlib.suppress(Exception):
                await self._ws.close()


class DeepgramProvider(STTProvider):
    def __init__(self, settings: Settings):
        if not settings.deepgram_api_key:
            raise RuntimeError("DEEPGRAM_API_KEY is not set in .env")
        self._key = settings.deepgram_api_key
        self._model = settings.deepgram_model

    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream:
        params = {
            "model": self._model,
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true",   # live partial text while someone is speaking
            "punctuate": "true",
            "smart_format": "true",
            "endpointing": 300,          # ms of silence that ends a turn (speech_final)
            "utterance_end_ms": 1000,    # backup turn end after a pause, robust to noise
        }
        url = f"{STREAMING_URL}?{urlencode(params)}"
        # Retry transient network failures (DNS hiccups, dropped connections) with backoff.
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                ws = await connect(url, additional_headers={"Authorization": f"Token {self._key}"},
                                   open_timeout=10)
                return DeepgramStream(ws, speaker)
            except InvalidStatus as exc:
                code = exc.response.status_code
                if code in (401, 403):
                    raise RuntimeError("Deepgram rejected the API key. Check DEEPGRAM_API_KEY in .env.") from exc
                raise RuntimeError(f"Deepgram refused the connection (HTTP {code}).") from exc
            except (TimeoutError, OSError) as exc:
                log.warning("Deepgram connect attempt %d/%d failed: %s", attempt, CONNECT_ATTEMPTS, exc)
                if attempt == CONNECT_ATTEMPTS:
                    raise RuntimeError(
                        "Can't reach the transcription service. Check your internet connection and try again."
                    ) from exc
                await asyncio.sleep(RETRY_BACKOFF_S * attempt)
        raise AssertionError("unreachable")


@register_stt("deepgram")
def _factory(settings: Settings) -> STTProvider:
    return DeepgramProvider(settings)
