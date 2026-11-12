import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.copilot import session as copilot_session
from app.main import app
from app.providers.registry import get_llm, get_stt
from tests.fakes import FakeLLM, FakeSTT


def test_health():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_session_routes_audio_and_forwards_transcripts(monkeypatch):
    fake = FakeSTT({"interviewer": [("tell me about", False), ("We use Python here.", True)]})
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: fake)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "sample_rate": 16000})
        assert ws.receive_json() == {"type": "ready", "channels": ["interviewer", "candidate"], "answers": True,
                                     "cv": "Sample CV", "jd": None, "session_id": None, "resumed": False,
                                     "remaining_seconds": None}

        ws.send_bytes(bytes([0]) + b"\x00" * 3200)  # 100 ms interviewer audio
        partial = ws.receive_json()
        final = ws.receive_json()
        assert partial == {"type": "transcript", "speaker": "interviewer", "text": "tell me about",
                           "is_final": False, "end_of_turn": False}
        assert final["is_final"] is True and final["text"] == "We use Python here."
        ws.send_json({"type": "end"})
    assert fake.streams["interviewer"].bytes == 3200
    assert fake.streams["candidate"].bytes == 0
    assert fake.streams["interviewer"].closed.is_set()


def test_missing_stt_key_reports_clear_error(monkeypatch):
    def boom(settings):
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set in .env")
    monkeypatch.setattr(copilot_session, "get_stt", boom)
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "ASSEMBLYAI_API_KEY" in msg["message"]


def test_missing_llm_key_keeps_transcription_running(monkeypatch):
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: get_llm(Settings(groq_api_key="")))
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        warning = ws.receive_json()
        assert warning["type"] == "warning" and "GROQ_API_KEY" in warning["message"]
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["answers"] is False


def test_providers_registered_and_require_keys():
    with pytest.raises(RuntimeError, match="ASSEMBLYAI_API_KEY"):
        get_stt(Settings(stt_provider="assemblyai", assemblyai_api_key=""))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_llm(Settings(llm_provider="groq", groq_api_key=""))


def test_unregistered_provider_fails_clearly():
    with pytest.raises(RuntimeError, match="not registered"):
        get_stt(Settings(stt_provider="does-not-exist"))
