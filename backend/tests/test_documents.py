import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from app.copilot import session as copilot_session
from app.documents.parsing import ParseError, extract_text
from app.main import app
from tests.conftest import make_docx, make_pdf
from tests.fakes import FakeLLM, FakeSTT

CV_LINES = [
    "Sara Khan - Data Engineer",
    "Experience: Built Airflow pipelines at Contoso Retail processing 3 million orders per day.",
    "Cut warehouse costs by 18 percent by moving batch jobs to spot instances.",
    "Skills: Python, Airflow, Spark, BigQuery, dbt",
]


# ---------- parsing ----------
def test_pdf_text_extracted():
    text = extract_text("sara_cv.pdf", make_pdf(CV_LINES))
    assert "Contoso Retail" in text and "18 percent" in text


def test_docx_paragraphs_and_tables_extracted():
    data = make_docx(CV_LINES[:2], table=[["Company", "Role"], ["Contoso Retail", "Data Engineer"]])
    text = extract_text("cv.docx", data)
    assert "Airflow pipelines" in text
    assert "Contoso Retail | Data Engineer" in text


@pytest.mark.parametrize("name,data,needle", [
    ("cv.exe", b"x" * 100, "Unsupported file type"),
    ("cv.pdf", b"not a pdf at all" * 10, "could not be read"),
    ("cv.docx", b"not a docx" * 10, "could not be read"),
    ("cv.txt", b"too short", "Almost no text"),
    ("cv.txt", b"a" * (4 * 1024 * 1024 + 1), "larger than 4 MB"),
])
def test_bad_files_give_clear_errors(name, data, needle):
    with pytest.raises(ParseError, match=needle):
        extract_text(name, data)


# ---------- API ----------
def test_upload_list_delete_flow(memory_repo):
    client = TestClient(app)
    r = client.post("/api/documents/upload", data={"kind": "cv"},
                    files={"file": ("Sara Khan CV.pdf", make_pdf(CV_LINES), "application/pdf")})
    assert r.status_code == 201, r.text
    cv = r.json()
    assert cv["kind"] == "cv" and cv["title"] == "Sara Khan CV" and "Contoso" in cv["preview"]

    r = client.post("/api/documents/text", json={"kind": "jd", "text": "Senior Data Engineer at Fabrikam. "
                                                 "Must have Airflow, Spark and strong SQL. Remote role."})
    assert r.status_code == 201
    jd = r.json()
    assert jd["title"].startswith("Job description")

    assert [d["id"] for d in client.get("/api/documents?kind=cv").json()] == [cv["id"]]
    assert len(client.get("/api/documents").json()) == 2

    assert client.delete(f"/api/documents/{cv['id']}").status_code == 204
    assert client.delete(f"/api/documents/{cv['id']}").status_code == 404
    assert client.delete("/api/documents/not-a-uuid").status_code == 404
    assert len(client.get("/api/documents").json()) == 1


def test_upload_rejects_unreadable_file(memory_repo):
    client = TestClient(app)
    r = client.post("/api/documents/upload", data={"kind": "cv"},
                    files={"file": ("scan.pdf", make_pdf([""]), "application/pdf")})
    assert r.status_code == 422 and "scanned PDF" in r.json()["detail"]


def test_documents_return_503_without_database():
    previous = getattr(app.state, "repo", None)
    app.state.repo = None
    try:
        r = TestClient(app).get("/api/documents")
        assert r.status_code == 503 and "Docker" in r.json()["detail"]
    finally:
        app.state.repo = previous


def test_other_users_documents_are_invisible(memory_repo):
    doc = asyncio.run(memory_repo.create("someone-else", "cv", "Theirs", "x" * 100))
    client = TestClient(app)
    assert client.get("/api/documents").json() == []
    assert client.delete(f"/api/documents/{doc.id}").status_code == 404


# ---------- session uses the chosen CV and JD ----------
def _start(ws, **extra):
    ws.send_json({"type": "start", **extra})
    return ws.receive_json()


def test_session_answers_from_chosen_cv_and_jd(monkeypatch, memory_repo):
    cv = asyncio.run(memory_repo.create("dev-user", "cv", "Sara CV", "\n".join(CV_LINES)))
    jd = asyncio.run(memory_repo.create("dev-user", "jd", "Fabrikam JD", "Fabrikam needs Spark and dbt experts."))
    stt = FakeSTT({"interviewer": [("Tell me about a pipeline you built?", True)]})
    llm = FakeLLM()
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: stt)
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ready = _start(ws, cv_id=cv.id, jd_id=jd.id, session_type="technical")
        assert ready["type"] == "ready" and ready["cv"] == "Sara CV" and ready["jd"] == "Fabrikam JD"
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        while ws.receive_json()["type"] != "answer_done":
            pass
        ws.send_json({"type": "end"})
    system = llm.chat_calls[0][0].content
    assert "Contoso Retail" in system and "Brightlane" not in system  # user's CV, not the sample
    assert "Fabrikam needs Spark and dbt experts." in system
    assert "This is a technical interview" in system


def test_session_refuses_missing_cv_instead_of_using_sample(monkeypatch, memory_repo):
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        msg = _start(ws, cv_id="00000000-0000-0000-0000-000000000000")
        assert msg["type"] == "error" and "not found" in msg["message"]


def test_session_refuses_jd_passed_as_cv(monkeypatch, memory_repo):
    jd = asyncio.run(memory_repo.create("dev-user", "jd", "A JD", "x" * 100))
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT())
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: FakeLLM())
    with TestClient(app).websocket_connect("/ws/session") as ws:
        assert _start(ws, cv_id=jd.id)["type"] == "error"


# ---------- real Postgres (runs when TEST_DATABASE_URL is set) ----------
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_repo_roundtrip_and_user_scoping():
    from app.documents.repo import PostgresDocumentRepo

    async def run():
        repo = await PostgresDocumentRepo.connect(os.environ["TEST_DATABASE_URL"])
        try:
            await repo.pool.execute("DELETE FROM documents WHERE user_id IN ('u1', 'u2')")
            a = await repo.create("u1", "cv", "CV A", "text a " * 20)
            await repo.create("u1", "jd", "JD A", "text b " * 20)
            await repo.create("u2", "cv", "Other user", "text c " * 20)
            assert [d.title for d in await repo.list("u1", "cv")] == ["CV A"]
            assert len(await repo.list("u1")) == 2
            got = await repo.get("u1", a.id)
            assert got and got.text.startswith("text a") and got.kind == "cv"
            assert await repo.get("u2", a.id) is None          # other user cannot read it
            assert await repo.get("u1", "not-a-uuid") is None
            assert await repo.delete("u2", a.id) is False      # other user cannot delete it
            assert await repo.delete("u1", a.id) is True
            assert await repo.get("u1", a.id) is None
            # Schema creation is idempotent (runs on every server start).
            await (await PostgresDocumentRepo.connect(os.environ["TEST_DATABASE_URL"])).close()
        finally:
            await repo.pool.execute("DELETE FROM documents WHERE user_id IN ('u1', 'u2')")
            await repo.close()

    asyncio.run(run())
