import asyncio
import os
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.copilot import session as copilot_session
from app.history.repo import InMemoryHistoryRepo
from app.main import app
from app.users.repo import InMemoryUserRepo, User
from tests.conftest import TEST_USER
from tests.fakes import FakeLLM, FakeSTT


@pytest.fixture
def stores(memory_repo):
    users, history = InMemoryUserRepo(), InMemoryHistoryRepo()
    users.users[TEST_USER] = User(TEST_USER, "test@example.com", "Test")
    prev = (getattr(app.state, "users", None), getattr(app.state, "history", None))
    app.state.users, app.state.history = users, history
    yield users, history
    app.state.users, app.state.history = prev


def _drain_until(ws, predicate, limit=100):
    out = []
    for _ in range(limit):
        m = ws.receive_json()
        out.append(m)
        if predicate(m):
            return out
    raise AssertionError(out)


def _end(ws):
    """End the session and wait until the server has finished (it closes the connection)."""
    ws.send_json({"type": "end"})
    with pytest.raises(WebSocketDisconnect):
        while True:
            ws.receive_json()


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ---------- recording ----------
def test_session_records_transcript_and_answers(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({
        "interviewer": [("Thanks for joining.", True), ("Why do you want this role?", True)]}))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM(tokens=["**Because", " growth.**"]))
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "session_type": "behavioral"})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["session_id"] and ready["remaining_seconds"] == 30 * 60
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        _drain_until(ws, lambda m: m["type"] == "answer_done")
        _end(ws)
    sid = ready["session_id"]
    assert _wait(lambda: history.sessions[sid]["summary"].ended_at is not None)
    detail = asyncio.run(history.get_session(TEST_USER, sid))
    assert [ln["text"] for ln in detail.lines] == ["Thanks for joining.", "Why do you want this role?"]
    assert detail.answers[0]["question"] == "Why do you want this role?"
    assert detail.answers[0]["answer"] == "**Because growth.**"
    assert detail.summary.session_type == "behavioral" and detail.summary.cv_title == "Sample CV"


def test_replaced_question_is_not_kept_in_history(monkeypatch, stores):
    _, history = stores
    llm = FakeLLM()

    async def detect(messages, tier="fast", max_tokens=200):
        latest = messages[-1].content.split("Latest interviewer turn:\n", 1)[-1]
        q = "Tell me about a time you convinced someone?" if "convinced" in latest else "Can you persuade people?"
        return {"is_question": True, "question": q, "category": "behavioral"}
    llm.complete_json = detect
    monkeypatch.setattr(copilot_session, "PARTIAL_CHECK_INTERVAL_S", 0)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({"interviewer": [
        ("Can you persuade people? Maybe tell me", False),
        ("Can you persuade people? Maybe tell me about a time you convinced someone? Yeah so", False)]}))
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        sid = ws.receive_json()["session_id"]
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        _drain_until(ws, lambda m: m["type"] == "question" and "replaces" in m)
        _drain_until(ws, lambda m: m["type"] == "answer_done")
        _end(ws)
    assert _wait(lambda: history.sessions[sid]["summary"].ended_at is not None)
    answers = asyncio.run(history.get_session(TEST_USER, sid)).answers
    assert [a["question"] for a in answers] == ["Tell me about a time you convinced someone?"]


# ---------- plan limits ----------
def test_no_minutes_left_blocks_start(monkeypatch, stores):
    _, history = stores
    sid = asyncio.run(history.create_session(TEST_USER, "mixed", "CV", None))
    asyncio.run(history.add_seconds(sid, 30 * 60))  # Free plan: 30 minutes, all used
    opened = []
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: opened.append(1) or FakeSTT())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["code"] == "limit" and "30 minutes" in msg["message"]
    assert opened == []  # no transcription was started (nothing was paid for)


def test_limit_reached_mid_session_ends_it(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(copilot_session, "METER_INTERVAL_S", 0.2)
    monkeypatch.setattr(copilot_session, "limit_seconds", lambda plan: 2)  # a 2-second plan
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ready = ws.receive_json()
        assert ready["remaining_seconds"] == 2
        msgs = _drain_until(ws, lambda m: m.get("code") == "limit")
        assert any(m["type"] == "warning" and "left on your plan" in m["message"] for m in msgs)
        assert any(m["type"] == "usage" for m in msgs)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4402
    sid = ready["session_id"]
    assert _wait(lambda: history.sessions[sid]["summary"].ended_at is not None)
    assert history.sessions[sid]["summary"].seconds_used >= 1  # time was recorded


def test_unlimited_plan_has_no_limit(monkeypatch, stores):
    users, _ = stores
    users.users[TEST_USER].plan = "premium"
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        assert ws.receive_json()["remaining_seconds"] is None
        ws.send_json({"type": "end"})


# ---------- API ----------
def test_usage_plans_and_history_api(stores):
    users, history = stores
    client = TestClient(app)
    sid = asyncio.run(history.create_session(TEST_USER, "technical", "My CV", "Some JD"))
    asyncio.run(history.add_seconds(sid, 600))
    asyncio.run(history.add_line(sid, 0, "interviewer", "What is a REST API?"))
    asyncio.run(history.save_answer(sid, "q1", "What is a REST API?", "technical", "**An HTTP interface.**", [], {}))
    other = asyncio.run(history.create_session("someone-else", "mixed", "Theirs", None))

    plans = client.get("/api/plans").json()
    assert [p["id"] for p in plans] == ["free", "pro", "premium"] and "price_setting" not in plans[1]
    usage = client.get("/api/usage").json()
    assert usage["plan"]["id"] == "free" and usage["used_seconds"] == 600
    assert usage["limit_seconds"] == 1800 and usage["remaining_seconds"] == 1200 and usage["resets_at"]

    listed = client.get("/api/sessions").json()
    assert [s["id"] for s in listed] == [sid] and listed[0]["questions"] == 1
    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["lines"][0]["text"] == "What is a REST API?" and detail["answers"][0]["category"] == "technical"
    assert client.get(f"/api/sessions/{other}").status_code == 404      # another user's interview
    assert client.delete(f"/api/sessions/{other}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get("/api/sessions").json() == []


def test_history_endpoints_need_database():
    prev = getattr(app.state, "history", None)
    app.state.history = None
    try:
        assert TestClient(app).get("/api/sessions").status_code == 503
    finally:
        app.state.history = prev


# ---------- Postgres ----------
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_history_roundtrip_and_cascade():
    from app.db.migrations import connect
    from app.history.repo import PostgresHistoryRepo
    from app.users.repo import PostgresUserRepo

    async def run():
        pool = await connect(os.environ["TEST_DATABASE_URL"])
        try:
            await pool.execute("DELETE FROM users WHERE email LIKE '%@histtest.example'")
            users, history = PostgresUserRepo(pool), PostgresHistoryRepo(pool)
            u = await users.create("a@histtest.example", "A", None)
            assert u.plan == "free"
            await users.set_plan(u.id, "pro")
            assert (await users.get(u.id)).plan == "pro"
            sid = await history.create_session(u.id, "behavioral", "CV A", "JD A")
            await history.add_line(sid, 0, "interviewer", "Tell me about yourself?")
            await history.add_line(sid, 0, "interviewer", "duplicate seq ignored")
            await history.add_line(sid, 1, "candidate", "Sure.")
            await history.save_answer(sid, "q1", "Tell me about yourself?", "behavioral", "v1", ["30%"], {"x": 1})
            await history.save_answer(sid, "q1", "Tell me about yourself?", "behavioral", "v2", [], {"x": 2})
            await history.save_answer(sid, "q2", "Why us?", "behavioral", "gone", [], {})
            await history.delete_answer(sid, "q2")
            await history.add_seconds(sid, 90)
            await history.add_seconds(sid, 30)
            await history.end_session(sid)
            assert await history.used_seconds_this_month(u.id) == 120
            d = await history.get_session(u.id, sid)
            assert [ln["text"] for ln in d.lines] == ["Tell me about yourself?", "Sure."]
            assert len(d.answers) == 1 and d.answers[0]["answer"] == "v2" and d.answers[0]["timings"] == {"x": 2}
            assert d.summary.ended_at is not None and d.summary.questions == 1
            other = await users.create("b@histtest.example", "B", None)
            assert await history.get_session(other.id, sid) is None
            assert not await history.delete_session(other.id, sid)
            assert [s.id for s in await history.list_sessions(u.id)] == [sid]
            await users.delete(u.id)  # cascade removes the history
            assert await pool.fetchval("SELECT COUNT(*) FROM interview_sessions WHERE id = $1::uuid", sid) == 0
            assert await pool.fetchval("SELECT COUNT(*) FROM session_lines WHERE session_id = $1::uuid", sid) == 0
        finally:
            await pool.execute("DELETE FROM users WHERE email LIKE '%@histtest.example'")
            await pool.close()

    asyncio.run(run())


def test_usage_accounting_carries_fractions():
    """Five 0.4 s ticks must record 2 seconds, not 5 x round(0.4) = 0 (the bug this guards against)."""
    recorded = []

    class Rec:
        def seconds(self, n):
            recorded.append(n)

    s = copilot_session.CopilotSession(None, copilot_session.Services(), TEST_USER)  # type: ignore[arg-type]
    s.recorder = Rec()  # type: ignore[assignment]
    for _ in range(5):
        s.session_s += 0.4
        s._record_usage()
    assert sum(recorded) == 2 and s.recorded_s == 2
