import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.copilot import session as copilot_session
from app.copilot.detector import detect_question, has_question_cue
from app.main import app
from tests.fakes import FakeLLM, FakeSTT


# ---------- cue filter ----------
@pytest.mark.parametrize("text", [
    "Could you tell me a little bit about yourself?",
    "Tell me about a time you handled conflict.",
    "Walk me through your last project",
    "So why exactly are you interested in our firm",
    "Describe how you would design a URL shortener",
    "How would you scale this service",
    "What motivates you",
])
def test_cue_filter_accepts_questions(text):
    assert has_question_cue(text)


@pytest.mark.parametrize("text", [
    "Great, thanks.",
    "That sounds like a very impressive background.",
    "Okay. Let's move on.",
    "I studied industrial engineering at Northeastern.",
])
def test_cue_filter_skips_statements(text):
    assert not has_question_cue(text)


def test_detector_skips_llm_call_for_statements():
    llm = FakeLLM()
    result = asyncio.run(detect_question(llm, [], "Great, thanks for that."))
    assert result is None and llm.json_calls == []


def test_detector_extracts_question_from_mixed_turn():
    llm = FakeLLM()
    turn = "Could you tell me a little bit about yourself? Yeah, absolutely. Thanks for having me."
    result = asyncio.run(detect_question(llm, [], turn))
    assert result is not None
    assert result.question == "Could you tell me a little bit about yourself?"
    assert result.category == "behavioral"


# ---------- full session flow ----------
def _drain_until(ws, kind: str, limit: int = 50) -> list[dict]:
    out = []
    for _ in range(limit):
        msg = ws.receive_json()
        out.append(msg)
        if msg["type"] == kind:
            return out
    raise AssertionError(f"never received {kind}: {out}")


def test_question_detected_and_answer_streamed(monkeypatch):
    stt = FakeSTT({"interviewer": [
        ("Thanks for joining today.", True),
        ("Why are you interested in this role? Well, I", True),
    ]})
    llm = FakeLLM(tokens=["**I want", " to grow.**", "\n- Situation"])
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)

    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)

        msgs = _drain_until(ws, "answer_done")
        kinds = [m["type"] for m in msgs]
        question = next(m for m in msgs if m["type"] == "question")
        tokens = "".join(m["text"] for m in msgs if m["type"] == "token")
        done = msgs[-1]

        assert question["text"] == "Why are you interested in this role?"
        assert question["category"] == "behavioral"
        assert kinds.index("question") < kinds.index("answer_start") < kinds.index("token")
        assert tokens == "**I want to grow.**\n- Situation"
        assert done["stopped"] is False
        assert done["timings"]["first_token_ms"] is not None
        assert done["timings"]["detect_ms"] is not None

        # Only the turn with a question cue reached the LLM; the pleasantry was filtered out.
        assert len(llm.json_calls) == 1
        # The answer prompt contains the CV and the question.
        system, user = llm.chat_calls[0]
        assert "Brightlane Logistics" in system.content
        assert "Why are you interested in this role?" in user.content

        # Regenerate streams a fresh answer for the same question.
        ws.send_json({"type": "regenerate"})
        again = _drain_until(ws, "answer_done")
        assert again[0] == {"type": "answer_start", "question_id": question["id"]}
        assert again[-1]["timings"]["detect_ms"] is None
        ws.send_json({"type": "end"})
    for _ in range(50):  # the server finishes cleanup just after the client disconnects
        if llm.closed:
            break
        time.sleep(0.05)
    assert llm.closed


def test_stop_cancels_answer(monkeypatch):
    stt = FakeSTT({"interviewer": [("Tell me about yourself?", True)]})
    llm = FakeLLM(tokens=[f" t{i}" for i in range(200)], token_delay=0.02)
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)

    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        _drain_until(ws, "token")
        ws.send_json({"type": "stop"})
        msgs = _drain_until(ws, "answer_done")
        assert msgs[-1]["stopped"] is True
        assert sum(1 for m in msgs if m["type"] == "token") < 199
        ws.send_json({"type": "end"})


def test_duplicate_question_not_answered_twice(monkeypatch):
    stt = FakeSTT({"interviewer": [("What is your biggest strength?", True),
                                   ("what is your biggest strength?", True),
                                   ("Okay, next one. How do you handle deadlines?", True)]})
    llm = FakeLLM()
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)

    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        questions = []
        for _ in range(60):
            m = ws.receive_json()
            if m["type"] == "question":
                questions.append(m["text"])
            if len(questions) == 2:
                break
        assert questions == ["What is your biggest strength?", "Okay, next one. How do you handle deadlines?"]
        ws.send_json({"type": "end"})


# ---------- mid-turn detection and fuzzy duplicates ----------
def test_question_detected_inside_turn_that_never_ends(monkeypatch):
    # Candidate answers on the same audio source, so the provider never ends the turn.
    stt = FakeSTT({"interviewer": [
        ("Ability to persuade people", False),
        ("Ability to persuade people. Maybe tell me about a time you were able to convince someone "
         "to change their mind? Yeah so what I can think of is", False),
    ]})
    llm = FakeLLM()
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        msgs = _drain_until(ws, "answer_done")
        q = next(m for m in msgs if m["type"] == "question")
        assert "convince someone to change their mind?" in q["text"]
        # The first partial had no question cue, so only one detection call was made.
        assert len(llm.json_calls) == 1
        ws.send_json({"type": "end"})


def test_same_question_from_partial_and_final_answered_once(monkeypatch):
    stt = FakeSTT({"interviewer": [
        ("So tell me about a time you led a team? Sure so at my last", False),
        ("So, tell me about a time you led a team? Sure, so at my last job I led five people.", True),
        ("Great. What is your biggest weakness?", True),
    ]})
    llm = FakeLLM()
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        questions = []
        for _ in range(80):
            m = ws.receive_json()
            if m["type"] == "question":
                questions.append(m["text"])
            if len(questions) == 2:
                break
        assert questions == ["So tell me about a time you led a team?", "Great. What is your biggest weakness?"]
        ws.send_json({"type": "end"})


def test_fuzzy_duplicate_detection():
    from app.copilot.detector import is_duplicate
    asked = ["Tell me about a time you were able to convince someone to change their mind?"]
    assert is_duplicate("Can you tell me about a time you convinced someone to change their mind?", asked)
    assert not is_duplicate("What is your biggest weakness?", asked)


def test_detector_receives_already_detected_questions():
    llm = FakeLLM()
    asyncio.run(detect_question(llm, [], "And what about teamwork?", already=["Why this role?"]))
    assert "- Why this role?" in llm.json_calls[0][1].content


def test_prompts_enforce_complete_questions_and_cv_only_facts():
    from app.copilot.prompts import ANSWER_SYSTEM, DETECT_SYSTEM
    assert "COMPLETE" in DETECT_SYSTEM and "return is_question false and wait" in DETECT_SYSTEM
    assert "Every number you write must appear in the CV exactly" in ANSWER_SYSTEM
    assert "{cv}" in ANSWER_SYSTEM
