import asyncio
import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import auth_routes
from app.auth import jwt_secret
from app.config import Settings
from app.copilot import session as copilot_session
from app.main import app
from app.users.repo import InMemoryUserRepo
from app.users.security import hash_password, verify_password
from tests.fakes import FakeLLM, FakeSTT

pytestmark = pytest.mark.real_auth


@pytest.fixture
def users(memory_repo):
    repo = InMemoryUserRepo()
    previous = getattr(app.state, "users", None)
    app.state.users = repo
    yield repo
    app.state.users = previous


@pytest.fixture
def client():
    return TestClient(app)


def register(client, email="ana@example.com", password="correct horse 1"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "name": "Ana"})
    assert r.status_code == 201, r.text
    return r.json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- passwords and tokens ----------
def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass" and verify_password("s3cret-pass", h)
    assert not verify_password("wrong-pass", h) and not verify_password("x", None)
    assert not verify_password("a" * 100, h)  # over bcrypt's 72-byte limit: rejected, never truncated


def test_jwt_secret_required_outside_development(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(app_env="production", jwt_secret=""))
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        jwt_secret()
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(jwt_secret="too-short"))
    with pytest.raises(RuntimeError, match="at least 32"):
        jwt_secret()


# ---------- register / login ----------
def test_register_then_me(users, client):
    out = register(client)
    assert out["user"]["email"] == "ana@example.com" and "password" not in str(out["user"]).lower().replace(
        "has_password", "")
    me = client.get("/api/me", headers=auth(out["access_token"]))
    assert me.status_code == 200 and me.json()["name"] == "Ana"
    stored = asyncio.run(users.get_by_email("ANA@example.com"))  # emails are case-insensitive
    assert stored and stored.password_hash.startswith("$2")    # bcrypt hash, never plain text


@pytest.mark.parametrize("email,password,status,needle", [
    ("not-an-email", "longenough1", 422, "valid email"),
    ("b@example.com", "short", 422, "at least 8"),
    ("b@example.com", "é" * 40, 422, "too long"),
])
def test_register_validation(users, client, email, password, status, needle):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == status and needle in r.json()["detail"]


def test_duplicate_email_rejected(users, client):
    register(client)
    r = client.post("/api/auth/register", json={"email": "Ana@Example.com", "password": "another-pass1"})
    assert r.status_code == 409


def test_login_success_and_generic_failures(users, client):
    register(client)
    ok = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "correct horse 1"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    wrong = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "nope-nope"})
    unknown = client.post("/api/auth/login", json={"email": "who@example.com", "password": "nope-nope"})
    # Same message either way: the response does not reveal which emails are registered.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Email or password is incorrect."


def test_login_rate_limited(users, client):
    register(client)
    for _ in range(auth_routes.MAX_FAILED):
        client.post("/api/auth/login", json={"email": "ana@example.com", "password": "bad-pass-1"})
    r = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "correct horse 1"})
    assert r.status_code == 429


# ---------- Google hand-off ----------
def test_google_requires_internal_secret_and_links_existing_account(users, client, monkeypatch):
    monkeypatch.setattr(auth_routes, "get_settings", lambda: Settings(internal_api_secret="s3", access_token_days=7))
    body = {"email": "ana@example.com", "name": "Ana G", "google_sub": "g-123"}
    assert client.post("/api/auth/google", json=body).status_code == 403
    assert client.post("/api/auth/google", json=body, headers={"X-Internal-Secret": "wrong"}).status_code == 403
    existing = register(client)
    r = client.post("/api/auth/google", json=body, headers={"X-Internal-Secret": "s3"})
    assert r.status_code == 200 and r.json()["user"]["id"] == existing["user"]["id"]  # one account, not two
    new = client.post("/api/auth/google", json={**body, "email": "new@example.com", "google_sub": "g-9"},
                      headers={"X-Internal-Secret": "s3"}).json()
    assert new["user"]["google"] is True and new["user"]["has_password"] is False
    pw = client.post("/api/auth/login", json={"email": "new@example.com", "password": "anything-1"})
    assert pw.status_code == 401 and "Google" in pw.json()["detail"]


# ---------- protected endpoints ----------
def test_endpoints_require_a_valid_token(users, client):
    assert client.get("/api/documents").status_code == 401
    assert client.get("/api/documents", headers=auth("garbage")).status_code == 401
    user = register(client)["user"]
    expired = jwt.encode({"sub": user["id"], "iat": 0, "exp": int(time.time()) - 10, "typ": "access"},
                         jwt_secret(), algorithm="HS256")
    r = client.get("/api/documents", headers=auth(expired))
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    forged = jwt.encode({"sub": user["id"], "exp": int(time.time()) + 999, "typ": "access"},
                        "a-different-secret-that-is-32-bytes-long", algorithm="HS256")
    assert client.get("/api/documents", headers=auth(forged)).status_code == 401


def test_users_cannot_see_each_others_documents(users, client):
    a = register(client, "a@example.com")["access_token"]
    b = register(client, "b@example.com")["access_token"]
    doc = client.post("/api/documents/text", headers=auth(a),
                      json={"kind": "jd", "text": "A job description long enough to be accepted by the API."}).json()
    assert [d["id"] for d in client.get("/api/documents", headers=auth(a)).json()] == [doc["id"]]
    assert client.get("/api/documents", headers=auth(b)).json() == []
    assert client.delete(f"/api/documents/{doc['id']}", headers=auth(b)).status_code == 404


def test_delete_account_removes_data_and_invalidates_token(users, client, memory_repo):
    out = register(client)
    token = out["access_token"]
    client.post("/api/documents/text", headers=auth(token),
                json={"kind": "jd", "text": "A job description long enough to be accepted by the API."})
    assert client.delete("/api/me", headers=auth(token)).status_code == 204
    assert asyncio.run(memory_repo.list(out["user"]["id"])) == []
    assert asyncio.run(users.get(out["user"]["id"])) is None
    assert client.get("/api/me", headers=auth(token)).status_code == 401  # token dies with the account


# ---------- live session ----------
def ticket(client, token) -> str:
    r = client.post("/api/session/ticket", headers=auth(token))
    assert r.status_code == 200 and r.json()["expires_in"] == 30
    return r.json()["ticket"]


def test_websocket_rejects_missing_bad_or_reused_ticket(users, client):
    token = register(client)["access_token"]
    t = ticket(client, token)
    with client.websocket_connect(f"/ws/session?ticket={t}") as ws:
        ws.send_json({"type": "end"})
    # An access token in the URL is no longer accepted, and a ticket works only once.
    for url in ("/ws/session", "/ws/session?ticket=garbage", f"/ws/session?token={token}",
                f"/ws/session?ticket={t}"):
        with client.websocket_connect(url) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "auth"


def test_ticket_needs_sign_in_and_expires(users, client, monkeypatch):
    assert client.post("/api/session/ticket").status_code == 401
    token = register(client)["access_token"]
    t = ticket(client, token)
    store = app.state.store
    # Expire it: an old ticket is useless even if it leaks.
    store._data[f"ws-ticket:{t}"] = (store._data[f"ws-ticket:{t}"][0], 0)
    with client.websocket_connect(f"/ws/session?ticket={t}") as ws:
        assert ws.receive_json()["code"] == "auth"


def test_websocket_runs_as_token_user(users, client, monkeypatch, memory_repo):
    out = register(client)
    cv = asyncio.run(memory_repo.create(out["user"]["id"], "cv", "Ana CV", "Ana worked at Contoso. " * 5))
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with client.websocket_connect(f"/ws/session?ticket={ticket(client, out['access_token'])}") as ws:
        ws.send_json({"type": "start", "cv_id": cv.id})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["cv"] == "Ana CV"
        ws.send_json({"type": "end"})
    other = register(client, "other@example.com")
    with client.websocket_connect(f"/ws/session?ticket={ticket(client, other['access_token'])}") as ws:
        ws.send_json({"type": "start", "cv_id": cv.id})  # someone else's CV
        assert ws.receive_json()["type"] == "error"


# ---------- Postgres ----------
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_users_and_migrations():
    from app.db.migrations import MIGRATIONS, connect, migrate
    from app.users.repo import EmailTaken, PostgresUserRepo

    async def run():
        pool = await connect(os.environ["TEST_DATABASE_URL"])
        try:
            await migrate(pool)  # running again is a no-op
            versions = [r["version"] for r in await pool.fetch("SELECT version FROM schema_migrations ORDER BY 1")]
            assert versions == [v for v, _ in MIGRATIONS]
            await pool.execute("DELETE FROM users WHERE email LIKE '%@pgtest.example'")
            repo = PostgresUserRepo(pool)
            u = await repo.create("Ana@PGTest.example", "Ana", hash_password("pw-12345678"))
            assert u.email == "ana@pgtest.example"
            with pytest.raises(EmailTaken):
                await repo.create("ana@pgtest.example", "Dup", None)
            linked = await repo.upsert_google("ana@pgtest.example", "Ana G", "sub-1")
            assert linked.id == u.id and linked.google_sub == "sub-1" and linked.name == "Ana"
            g = await repo.upsert_google("g@pgtest.example", "Gina", "sub-2")
            assert g.password_hash is None and (await repo.get(g.id)).email == "g@pgtest.example"
            assert await repo.get("not-a-uuid") is None
            assert await repo.delete(u.id) and await repo.get(u.id) is None
        finally:
            await pool.execute("DELETE FROM users WHERE email LIKE '%@pgtest.example'")
            await pool.close()

    asyncio.run(run())
