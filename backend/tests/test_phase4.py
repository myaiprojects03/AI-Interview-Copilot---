"""Phase 4: shared state (Redis), reconnect/resume, transcription reconnect, latency report, security."""
import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.copilot import session as copilot_session
from app.history.repo import InMemoryHistoryRepo
from app.main import app
from app.providers.base import Speaker, STTProvider, STTStream
from app.shared.store import MemoryStore, RedisStore
from app.users.repo import InMemoryUserRepo, User
from tests.conftest import TEST_USER
from tests.fakes import FakeLLM, FakeStream, FakeSTT
from tests.test_history import _drain_until, _end, _wait


@pytest.fixture
def stores(memory_repo):
    users, history = InMemoryUserRepo(), InMemoryHistoryRepo()
    users.users[TEST_USER] = User(TEST_USER, "test@example.com", "Test")
    prev = (getattr(app.state, "users", None), getattr(app.state, "history", None))
    app.state.users, app.state.history = users, history
    yield users, history
    app.state.users, app.state.history = prev


# ---------- shared store ----------
async def _exercise_store(store):
    assert await store.hit("t:a", 60) == 1
    assert await store.hit("t:a", 60) == 2
    assert await store.count("t:a") == 2
    await store.clear("t:a")
    assert await store.count("t:a") == 0
    await store.put("t:ticket", "user-1", 30)
    assert await store.take("t:ticket") == "user-1"
    assert await store.take("t:ticket") is None  # single use


def test_memory_store():
    asyncio.run(_exercise_store(MemoryStore()))


@pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="TEST_REDIS_URL not set")
def test_redis_store():
    from redis.asyncio import Redis

    async def run():
        client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        store = RedisStore(client)
        try:
            await client.delete("copilot:t:a", "copilot:t:ticket")
            await _exercise_store(store)
            await store.hit("t:a", 60)
            assert 0 < await client.ttl("copilot:t:a") <= 60
            await store.hit("t:a", 600)  # the window is set by the first event only
            assert await client.ttl("copilot:t:a") <= 60
        finally:
            await client.delete("copilot:t:a")
            await store.aclose()

    asyncio.run(run())


def test_rate_limits_are_shared_across_instances():
    """Two app instances using one store see the same failure count."""
    from app.api import auth_routes
    store = MemoryStore()  # stands in for the shared Redis

    async def run():
        key = auth_routes._key("login-failures", "a@example.com", "1.2.3.4")
        for _ in range(auth_routes.MAX_FAILED):
            await auth_routes._record(store, key, auth_routes.WINDOW_S)  # spread over "instances"
        return await auth_routes._over_limit(store, key, auth_routes.MAX_FAILED)

    assert asyncio.run(run()) is True


def test_signups_are_limited_per_network(memory_repo):
    from app.api import auth_routes
    prev = getattr(app.state, "users", None)
    app.state.users = InMemoryUserRepo()
    try:
        client = TestClient(app)
        for i in range(auth_routes.MAX_SIGNUPS):
            r = client.post("/api/auth/register", json={"email": f"u{i}@example.com", "password": "pw-12345678"})
            assert r.status_code == 201
        r = client.post("/api/auth/register", json={"email": "one-more@example.com", "password": "pw-12345678"})
        assert r.status_code == 429
    finally:
        app.state.users = prev


def test_rate_limit_fails_open_when_redis_is_down():
    from app.api import auth_routes

    class Down(MemoryStore):
        async def count(self, key):
            raise ConnectionError("redis down")

    assert asyncio.run(auth_routes._over_limit(Down(), "k", 1)) is False


# ---------- security headers ----------
def test_security_headers():
    r = TestClient(app).get("/api/plans")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"


# ---------- reconnect: resume the same interview ----------
def _start(ws, **extra):
    ws.send_json({"type": "start", "sample_rate": 16000, **extra})
    ready = ws.receive_json()
    assert ready["type"] == "ready"
    return ready


def test_dropped_connection_resumes_the_same_interview(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({
        "interviewer": [("Why do you want this role?", True)]}))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        first = _start(ws)
        sid = first["session_id"]
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        _drain_until(ws, lambda m: m["type"] == "answer_done")
        _end(ws)

    # The same question heard again after reconnecting is not answered twice, and the transcript
    # continues in the same record.
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({
        "interviewer": [("Why do you want this role?", True), ("And what about teamwork?", True)]}))
    with TestClient(app).websocket_connect("/ws/session") as ws:
        again = _start(ws, resume=sid)
        assert again["session_id"] == sid and again["resumed"] is True
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        msgs = _drain_until(ws, lambda m: m["type"] == "answer_done")
        questions = [m["text"] for m in msgs if m["type"] == "question"]
        assert questions == ["And what about teamwork?"]
        _end(ws)

    assert _wait(lambda: history.sessions[sid]["summary"].ended_at is not None)
    s = history.sessions[sid]
    assert [line["seq"] for line in sorted(s["lines"].values(), key=lambda x: x["seq"])] == [0, 1, 2]
    assert len(s["answers"]) == 2 and len(history.sessions) == 1


def test_resume_is_refused_for_old_or_foreign_sessions(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    old = asyncio.run(history.create_session(TEST_USER, "mixed", "CV", None))
    asyncio.run(history.end_session(old))
    history.sessions[old]["summary"].ended_at = datetime.now(UTC) - timedelta(minutes=10)
    foreign = asyncio.run(history.create_session("someone-else", "mixed", "CV", None))
    for sid in (old, foreign, "not-a-session"):
        with TestClient(app).websocket_connect("/ws/session") as ws:
            ready = _start(ws, resume=sid)
            assert ready["resumed"] is False and ready["session_id"] not in (old, foreign)
            _end(ws)
    assert history.sessions[foreign]["summary"].ended_at is None  # untouched


def test_old_connection_cannot_end_a_resumed_session():
    history = InMemoryHistoryRepo()

    async def run():
        sid = await history.create_session(TEST_USER, "mixed", "CV", None, connection_id="a")
        assert await history.resume_session(TEST_USER, sid, "b", 120, 12) is not None
        await history.end_session(sid, "a")      # the dropped connection cleans up late
        assert history.sessions[sid]["summary"].ended_at is None
        await history.end_session(sid, "b")
        assert history.sessions[sid]["summary"].ended_at is not None

    asyncio.run(run())


# ---------- transcription service drops ----------
class DroppingStream(FakeStream):
    async def events(self):
        raise ConnectionError("provider closed the connection")
        yield  # pragma: no cover


class FlakySTT(STTProvider):
    """The first `drops` streams for the interviewer fail at once; later ones work."""

    def __init__(self, drops: int, script: list[tuple[str, bool]]):
        self.drops = drops
        self.script = script
        self.opened = 0

    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream:
        if speaker != "interviewer":
            return FakeStream(speaker, [])
        self.opened += 1
        if self.opened <= self.drops:
            return DroppingStream(speaker, [])
        return FakeStream(speaker, self.script)


def test_transcription_reconnects_after_a_drop(monkeypatch, stores):
    monkeypatch.setattr(copilot_session, "STT_RETRY_DELAYS_S", (0, 0, 0))
    stt = FlakySTT(drops=2, script=[("Tell me about yourself?", True)])
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        _start(ws)
        msgs = _drain_until(ws, lambda m: m["type"] == "info")
        msgs += _drain_until(ws, lambda m: m["type"] == "info")
        assert [m["type"] for m in msgs if m["type"] in ("warning", "info")] == ["warning", "info", "warning", "info"]
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)  # the reopened stream works
        _drain_until(ws, lambda m: m["type"] == "answer_done")
        _end(ws)
    assert stt.opened == 3


def test_transcription_gives_up_after_three_attempts(monkeypatch, stores):
    monkeypatch.setattr(copilot_session, "STT_RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FlakySTT(drops=99, script=[]))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        _start(ws)
        msgs = _drain_until(ws, lambda m: m["type"] == "error")
        assert sum(m["type"] == "warning" for m in msgs) == 3
        assert "could not be restarted" in msgs[-1]["message"]
        _end(ws)


# ---------- latency report ----------
def test_latency_report_is_for_admins_only(monkeypatch, stores):
    users, history = stores
    monkeypatch.setattr(get_settings(), "admin_emails", "boss@example.com")
    from app.api import history_routes
    from app.auth import current_user_id

    async def run():
        sid = await history.create_session(TEST_USER, "mixed", "CV", None)
        for i, first in enumerate([800, 1200, 1500, 2600]):
            await history.save_answer(sid, f"q{i}", "Q?", "behavioral", "A", [],
                                      {"detect_ms": 300, "retrieval_ms": None, "first_token_ms": first,
                                       "total_ms": first + 2000})

    asyncio.run(run())
    client = TestClient(app)
    assert client.get("/api/admin/latency").status_code == 403  # test user is not an admin

    users.users["boss"] = User("boss", "boss@example.com", "Boss")
    app.dependency_overrides[current_user_id] = lambda: "boss"
    try:
        r = client.get("/api/admin/latency?days=7")
        assert r.status_code == 200
        body = r.json()
        assert body["answers"] == 4 and body["first_words_under_2s"] == 0.75
        assert body["metrics"]["first_token_ms"]["p50"] == 1350
        assert body["metrics"]["first_token_ms"]["max"] == 2600
        assert body["metrics"]["retrieval_ms"]["p50"] is None
        assert client.get("/api/me").json()["is_admin"] is True
        assert client.get("/api/admin/latency?days=0").status_code == 422
    finally:
        app.dependency_overrides.pop(current_user_id, None)
    assert history_routes  # imported for the route


# ---------- Postgres ----------
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_resume_and_latency():
    from app.db.migrations import connect
    from app.history.repo import PostgresHistoryRepo
    from app.users.repo import PostgresUserRepo

    async def run():
        pool = await connect(os.environ["TEST_DATABASE_URL"])
        try:
            await pool.execute("DELETE FROM users WHERE email = 'resume@pgtest.example'")
            u = await PostgresUserRepo(pool).create("resume@pgtest.example", "R", None)
            h = PostgresHistoryRepo(pool)
            sid = await h.create_session(u.id, "mixed", "CV", None, connection_id="a")
            for i, (who, text) in enumerate([("interviewer", "Hi?"), ("candidate", "Hello"), ("interviewer", "Why?")]):
                await h.add_line(sid, i, who, text)
            await h.save_answer(sid, "q1", "Why?", "behavioral", "Because", [],
                                {"detect_ms": 200, "retrieval_ms": 90, "first_token_ms": 1000, "total_ms": 3000})
            await h.end_session(sid, "a")
            r = await h.resume_session(u.id, sid, "b", 120, 2)
            assert r is not None and r.next_seq == 3 and r.questions == ["Why?"]
            assert r.recent_lines == [("candidate", "Hello"), ("interviewer", "Why?")]
            await h.end_session(sid, "a")  # stale connection: ignored
            assert (await h.get_session(u.id, sid)).summary.ended_at is None
            await h.end_session(sid, "b")
            assert await h.resume_session("00000000-0000-0000-0000-000000000000", sid, "c", 120, 2) is None
            await pool.execute("UPDATE interview_sessions SET ended_at = now() - interval '10 minutes' WHERE id = $1",
                               __import__("uuid").UUID(sid))
            assert await h.resume_session(u.id, sid, "c", 120, 2) is None

            stats = await h.latency_stats(datetime.now(UTC) - timedelta(minutes=5))
            assert stats.answers >= 1 and stats.metrics["first_token_ms"]["p50"] is not None
            assert set(stats.metrics) == {"detect_ms", "retrieval_ms", "first_token_ms", "total_ms"}
            await pool.execute("DELETE FROM users WHERE id = $1", __import__("uuid").UUID(u.id))
        finally:
            await pool.close()

    asyncio.run(run())


def test_provider_errors_are_plain_outside_development(monkeypatch):
    from app.providers.base import LLMError
    settings = get_settings()
    secret_detail = RuntimeError("AssemblyAI rejected the API key. Check ASSEMBLYAI_API_KEY in .env.")
    assert "ASSEMBLYAI_API_KEY" in copilot_session.public_error(secret_detail, "fallback")  # development
    monkeypatch.setattr(settings, "app_env", "production")
    assert copilot_session.public_error(secret_detail, "Try again later.") == "Try again later."
    busy = LLMError("Groq rate limit reached. Wait a few seconds and press Regenerate.")
    assert "busy" in copilot_session.public_error(busy, "x")


def test_local_stripe_test_secret_is_refused_outside_development(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_local_test")
    monkeypatch.setattr(settings, "app_env", "production")
    r = TestClient(app).post("/api/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
    assert r.status_code == 503


# ---------- starting on a slow or failing network ----------
class SlowSTT(STTProvider):
    """Each stream takes `delay` seconds to connect; speakers in `fail` cannot connect at all."""

    def __init__(self, delay: float = 0.0, fail: tuple[str, ...] = ()):
        self.delay, self.fail = delay, fail

    async def open_stream(self, speaker: Speaker, sample_rate: int = 16000) -> STTStream:
        await asyncio.sleep(self.delay)
        if speaker in self.fail:
            raise RuntimeError("Can't reach the transcription service.")
        return FakeStream(speaker, [])


def test_streams_connect_in_parallel(monkeypatch, stores):
    import time
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: SlowSTT(delay=0.6))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        t = time.monotonic()
        ready = _start(ws)
        assert time.monotonic() - t < 1.0  # both 0.6 s connections overlap (one after the other: 1.2 s)
        assert ready["channels"] == ["interviewer", "candidate"]
        _end(ws)


def test_interviewer_stream_failure_stops_before_recording(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: SlowSTT(fail=("interviewer",)))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "sample_rate": 16000})
        msg = ws.receive_json()
        assert msg["type"] == "error" and "transcription of the interviewer" in msg["message"]
        ws.send_json({"type": "end"})
    assert history.sessions == {}  # no history record, no minutes used


def test_candidate_stream_failure_continues_with_the_interviewer(monkeypatch, stores):
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: SlowSTT(fail=("candidate",)))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start", "sample_rate": 16000})
        warning = ws.receive_json()
        assert warning["type"] == "warning" and "transcription of you" in warning["message"]
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["channels"] == ["interviewer"]
        _end(ws)


def test_hosted_postgres_connection_strings_work_as_shown():
    from app.db.migrations import normalize_dsn
    neon = ("postgresql://app:pw@ep-cool-1234-pooler.us-east-1.aws.neon.tech/neondb"
            "?sslmode=require&channel_binding=require")
    assert normalize_dsn(neon) == "postgresql://app:pw@ep-cool-1234.us-east-1.aws.neon.tech/neondb?sslmode=require"
    local = "postgresql://copilot:copilot@localhost:5432/copilot"
    assert normalize_dsn(local) == local


# ---------- hosts that cut long connections (Vercel: 300 s) ----------
def test_server_asks_for_a_fresh_connection_at_a_quiet_moment_and_the_handover_keeps_one_record(monkeypatch, stores):
    _, history = stores
    monkeypatch.setattr(get_settings(), "session_rotate_s", 1)
    monkeypatch.setattr(copilot_session, "ROTATE_QUIET_S", 0.2)
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/session") as old:
        sid = _start(old)["session_id"]
        msgs = _drain_until(old, lambda m: m["type"] == "rotate")
        assert msgs[-1] == {"type": "rotate"}
        # The browser opens the fresh connection while the old one is still open...
        with client.websocket_connect("/ws/session") as new:
            ready = _start(new, resume=sid)
            assert ready["session_id"] == sid and ready["resumed"] is True
            _end(old)  # ...then closes the old one
            assert history.sessions[sid]["summary"].ended_at is None  # still live on the new connection
            _end(new)
    assert _wait(lambda: history.sessions[sid]["summary"].ended_at is not None)
    assert len(history.sessions) == 1


def test_no_rotation_while_an_answer_is_being_written(monkeypatch, stores):
    monkeypatch.setattr(get_settings(), "session_rotate_s", 1)
    monkeypatch.setattr(copilot_session, "ROTATE_QUIET_S", 0.2)
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({
        "interviewer": [("Tell me about a time you led a team?", True)]}))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM(tokens=["word "] * 40, token_delay=0.05))
    with TestClient(app).websocket_connect("/ws/session") as ws:
        _start(ws)
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        msgs = _drain_until(ws, lambda m: m["type"] == "rotate")
        kinds = [m["type"] for m in msgs]
        assert "answer_done" in kinds and kinds.index("answer_done") < kinds.index("rotate")
        _end(ws)


def test_no_rotation_by_default(monkeypatch, stores):
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    assert get_settings().session_rotate_s == 0
