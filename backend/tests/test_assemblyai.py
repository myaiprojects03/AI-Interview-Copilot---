import asyncio
import socket

import pytest

from app.config import Settings
from app.providers import assemblyai_stt
from app.providers.assemblyai_stt import AssemblyAIProvider, AssemblyAIStream


def _provider():
    return AssemblyAIProvider(Settings(assemblyai_api_key="k"))


def test_retries_transient_dns_failure_then_connects(monkeypatch):
    calls = []

    async def flaky_connect(url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise socket.gaierror(8, "nodename nor servname provided, or not known")
        return object()

    monkeypatch.setattr(assemblyai_stt, "connect", flaky_connect)
    monkeypatch.setattr(assemblyai_stt, "RETRY_BACKOFF_S", 0)
    stream = asyncio.run(_provider().open_stream("interviewer"))
    assert isinstance(stream, AssemblyAIStream)
    assert len(calls) == 3
    assert "speech_model=universal-streaming-english" in calls[0] and "sample_rate=16000" in calls[0]


def test_gives_clear_message_when_network_stays_down(monkeypatch):
    async def down(url, **kwargs):
        raise socket.gaierror(8, "nodename nor servname provided, or not known")

    monkeypatch.setattr(assemblyai_stt, "connect", down)
    monkeypatch.setattr(assemblyai_stt, "RETRY_BACKOFF_S", 0)
    with pytest.raises(RuntimeError, match="Check your internet connection"):
        asyncio.run(_provider().open_stream("interviewer"))
