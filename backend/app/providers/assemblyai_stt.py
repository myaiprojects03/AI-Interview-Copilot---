"""AssemblyAI Universal-Streaming (v3) speech-to-text provider.

Browser -> our backend -> AssemblyAI. The API key stays on the server (Authorization header).
Docs: https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.config import Settings
from app.providers.base import Speaker, STTProvider, STTStream, TranscriptEvent
from app.providers.registry import register_stt

log = logging.getLogger(__name__)

STREAMING_URL = "wss://streaming.assemblyai.com/v3/ws"
CONNECT_ATTEMPTS = 3
RETRY_BACKOFF_S = 0.5


class AssemblyAIStream(STTStream):
    def __init__(self, ws: ClientConnection, speaker: Speaker):
        self._ws = ws
        self._speaker = speaker
        self._closed = False

    async def send_audio(self, chunk: bytes) -> None:
        if self._closed:
            return
        try:
            await self._ws.send(chunk)
        except ConnectionClosed:
            self._closed = True

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Turn":
                    text = (msg.get("transcript") or "").strip()
                    if not text:
                        continue
                    end_of_turn = bool(msg.get("end_of_turn"))
                    formatted = bool(msg.get("turn_is_formatted"))
                    words = msg.get("words") or []
                    # With format_turns=true, a turn ends with an unformatted message followed by a
                    # formatted one. Only the formatted end-of-turn message is treated as final.
                    is_final = end_of_turn and formatted
                    yield TranscriptEvent(
                        speaker=self._speaker,
                        text=text,
                        is_final=is_final,
                        speech_final=end_of_turn,
                        start=(words[0]["start"] / 1000) if words else 0.0,
                        end=(words[-1]["end"] / 1000) if words else 0.0,
                    )
                elif kind == "Begin":
                    log.info("AssemblyAI %s stream started: %s", self._speaker, msg.get("id"))
                elif kind == "Termination":
                    log.info("AssemblyAI %s stream ended: %.1fs audio", self._speaker,
                             msg.get("audio_duration_seconds", 0))
                    break
                elif kind == "Error" or "error" in msg:
                    raise RuntimeError(f"AssemblyAI error: {msg.get('error', msg)}")
        except ConnectionClosed as exc:
            if exc.rcvd and exc.rcvd.code not in (1000, 1001):
                raise RuntimeError(f"AssemblyAI closed the stream: {exc.rcvd.code} {exc.rcvd.reason}") from exc
        finally:
            self._closed = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.send(json.dumps({"type": "Terminate"}))
            # Give the server a moment to flush the final turn and send Termination.
            await asyncio.wait_for(self._ws.wait_closed(), timeout=3)
        except (TimeoutError, ConnectionClosed):
            pass
        finally:
            await self._ws.close()


class AssemblyAIProvider(STTProvider):
    def __init__(self, settings: Settings):
        if not settings.assemblyai_api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY is not set in .env")
        self._key = settings.assemblyai_api_key
        self._model = settings.assemblyai_speech_model

    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream:
        params = {
            "speech_model": self._model,
            "sample_rate": sample_rate,
            "encoding": "pcm_s16le",
            "format_turns": "true",
        }
        url = f"{STREAMING_URL}?{urlencode(params)}"
        # Retry transient network failures (DNS hiccups, dropped connections) with backoff.
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                ws = await connect(url, additional_headers={"Authorization": self._key}, open_timeout=10)
                return AssemblyAIStream(ws, speaker)
            except InvalidStatus as exc:
                code = exc.response.status_code
                if code in (401, 403):
                    raise RuntimeError("AssemblyAI rejected the API key. Check ASSEMBLYAI_API_KEY in .env.") from exc
                raise RuntimeError(f"AssemblyAI refused the connection (HTTP {code}).") from exc
            except (TimeoutError, OSError) as exc:
                log.warning("AssemblyAI connect attempt %d/%d failed: %s", attempt, CONNECT_ATTEMPTS, exc)
                if attempt == CONNECT_ATTEMPTS:
                    raise RuntimeError(
                        "Can't reach the transcription service. Check your internet connection and try again."
                    ) from exc
                await asyncio.sleep(RETRY_BACKOFF_S * attempt)
        raise AssertionError("unreachable")


@register_stt("assemblyai")
def _factory(settings: Settings) -> STTProvider:
    return AssemblyAIProvider(settings)
