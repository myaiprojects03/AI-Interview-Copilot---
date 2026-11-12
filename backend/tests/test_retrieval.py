import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.copilot import session as copilot_session
from app.copilot.factcheck import unverified_numbers
from app.copilot.prompts import answer_system_for, framework_topic
from app.documents.repo import Document
from app.main import app
from app.providers.pinecone_store import PineconeError, PineconeStore
from app.retrieval.chunking import chunk_text
from app.retrieval.service import RetrievalService, chunk_ids
from tests.conftest import make_docx
from tests.fakes import FakeEmbedder, FakeLLM, FakeSTT, MemoryVectorStore

EVAL = Path(__file__).resolve().parents[1] / "eval"
CV = (EVAL / "test_cv.txt").read_text()
JD = (EVAL / "test_jd.txt").read_text()


# ---------- chunking ----------
def test_chunks_keep_every_section_with_its_heading():
    chunks = chunk_text(CV)
    joined = "\n".join(chunks)
    for fact in ["40 alternative suppliers", "$2M", "120 critical parts", "30 to 85", "Riverside Technical",
                 "Power BI", "energy and education"]:
        assert fact in joined, fact
    assert any(c.startswith("Operations Analyst — Northwind Energy") and "12%" in c for c in chunks)
    assert any(c.startswith("Skills:") and "Excel, SQL" in c for c in chunks)
    assert all(len(c) <= 700 + 80 for c in chunks)


def test_long_section_is_split():
    text = "Experience\n" + " ".join(f"Did task number {i} well." for i in range(200))
    chunks = chunk_text(text)
    assert len(chunks) > 3 and all(c.startswith("Experience:") for c in chunks)


def test_docx_tables_stay_in_document_order():
    from app.documents.parsing import extract_text
    data = make_docx(["Summary", "Engineer with long experience in data systems.", "Skills"],
                     table=[["Analytics", "SQL, Python"]])
    text = extract_text("cv.docx", data)
    assert text.index("Skills") < text.index("Analytics | SQL, Python")


# ---------- fact check ----------
@pytest.mark.parametrize("answer,flagged", [
    ("Cut project cost by 12% and delayed the $2M purchase; machines at 55%.", []),
    ("We reduced response time by 30% and saved $15K/month.", ["30%", "$15K"]),   # last week's real failure
    ("Grew the club from 30 to 85 members over 2019.", []),
    ("Saved $2,000,000.", []),
    ("I have four years of experience.", []),
])
def test_unverified_numbers(answer, flagged):
    assert unverified_numbers(answer, [CV]) == flagged


# ---------- retrieval service ----------
def _service(delay: float = 0.0) -> tuple[RetrievalService, MemoryVectorStore]:
    store = MemoryVectorStore(delay=delay)
    return RetrievalService(FakeEmbedder(), store), store


def test_index_retrieve_and_delete_scoped_to_user_and_documents():
    svc, store = _service()
    cv = Document("cv1", "u1", "cv", "CV", CV)
    jd = Document("jd1", "u1", "jd", "JD", JD)
    other = Document("cv2", "u2", "cv", "Other", "Supplier negotiation at Acme with 40 suppliers.")

    async def run():
        n = await svc.index_document(cv)
        await svc.index_document(jd)
        await svc.index_document(other)
        assert n == len(chunk_text(CV))
        hits = await svc.retrieve("u1", "persuaded the plant director to delay the equipment purchase",
                                  ["cv1", "jd1"], top_k=2)
        assert "$2M" in hits[0].text  # the persuasion bullet ranks first
        assert all(h.metadata["doc_id"] in ("cv1", "jd1") for h in hits)
        only_jd = await svc.retrieve("u1", "suppliers", ["jd1"])
        assert all(h.metadata["doc_id"] == "jd1" for h in only_jd)
        assert all(h.metadata["doc_id"] == "cv2" for h in await svc.retrieve("u2", "suppliers", ["cv2"]))
        await svc.delete_document(cv)
        assert not any(i.startswith("cv1#") for i in store.data["u1"])
        assert set(chunk_ids(jd)) <= set(store.data["u1"])

    asyncio.run(run())


def test_ensure_indexed_only_indexes_missing_documents():
    svc, store = _service()
    doc = Document("cv1", "u1", "cv", "CV", CV)

    async def run():
        await svc.ensure_indexed(doc)
        first = dict(store.data["u1"])
        store.upsert_calls = 0
        orig = store.upsert

        async def counting(*a, **k):
            store.upsert_calls += 1
            await orig(*a, **k)
        store.upsert = counting
        await svc.ensure_indexed(doc)
        assert store.upsert_calls == 0 and store.data["u1"] == first

    asyncio.run(run())


# ---------- Pinecone REST client (mocked HTTP) ----------
class PineconeMock:
    def __init__(self, exists: bool = False, dimension: int = 384, not_ready_polls: int = 1, status: int = 200):
        self.exists = exists
        self.dimension = dimension
        self.not_ready = not_ready_polls
        self.status = status
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, str(req.url), body))
        assert req.headers["Api-Key"] == "k" and req.headers["X-Pinecone-Api-Version"] == "2025-10"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        url = str(req.url)
        if url == "https://api.pinecone.io/indexes/interview-copilot":
            if not self.exists:
                return httpx.Response(404, json={})
            ready = self.not_ready <= 0
            self.not_ready -= 1
            return httpx.Response(200, json={"dimension": self.dimension, "host": "ic-abc.svc.pinecone.io",
                                             "status": {"ready": ready}})
        if url == "https://api.pinecone.io/indexes" and req.method == "POST":
            self.exists = True
            return httpx.Response(201, json={})
        if url.endswith("/vectors/upsert"):
            return httpx.Response(200, json={"upsertedCount": len(body["vectors"])})
        if url.endswith("/query"):
            return httpx.Response(200, json={"matches": [
                {"id": "d#0", "score": 0.91, "metadata": {"doc_id": "d", "text": "Persuaded the director"}}]})
        if url.endswith("/vectors/delete"):
            return httpx.Response(200, json={})
        if "/vectors/fetch" in url:
            return httpx.Response(200, json={"vectors": {"d#0": {}} if "d%230" in url else {}})
        return httpx.Response(500, json={})


def _store(mock: PineconeMock) -> PineconeStore:
    s = PineconeStore(Settings(pinecone_api_key="k"), 384, transport=httpx.MockTransport(mock))
    s.ready_poll_s = 0
    return s


def test_pinecone_creates_index_waits_until_ready_then_upserts_in_batches():
    mock = PineconeMock(exists=False, not_ready_polls=2)
    store = _store(mock)
    asyncio.run(store.upsert("u1", [f"d#{i}" for i in range(150)], [[0.1] * 384] * 150, [{}] * 150))
    create = next(c for c in mock.calls if c[0] == "POST" and c[1] == "https://api.pinecone.io/indexes")
    assert create[2]["dimension"] == 384
    assert create[2]["spec"] == {"serverless": {"cloud": "aws", "region": "us-east-1"}}
    upserts = [c for c in mock.calls if c[1].endswith("/vectors/upsert")]
    assert [len(c[2]["vectors"]) for c in upserts] == [100, 50]
    assert all(c[1].startswith("https://ic-abc.svc.pinecone.io") and c[2]["namespace"] == "u1" for c in upserts)


def test_pinecone_query_filters_by_document_and_parses_matches():
    mock = PineconeMock(exists=True, not_ready_polls=0)
    store = _store(mock)
    hits = asyncio.run(store.query("u1", [0.1] * 384, top_k=3, doc_ids=["d"]))
    q = next(c for c in mock.calls if c[1].endswith("/query"))
    assert q[2]["filter"] == {"doc_id": {"$in": ["d"]}} and q[2]["topK"] == 3 and q[2]["namespace"] == "u1"
    assert hits[0].text == "Persuaded the director" and hits[0].score == pytest.approx(0.91)
    assert asyncio.run(store.exists("u1", "d#0")) is True
    assert asyncio.run(store.exists("u1", "x#0")) is False
    # The index host is looked up once and cached.
    assert sum(1 for c in mock.calls if c[1].endswith("/indexes/interview-copilot")) == 1


def test_pinecone_bad_key_and_dimension_mismatch_have_clear_errors():
    with pytest.raises(PineconeError, match="PINECONE_API_KEY"):
        asyncio.run(_store(PineconeMock(status=401)).query("u1", [0.0] * 384))
    with pytest.raises(PineconeError, match="dimension 1536"):
        asyncio.run(_store(PineconeMock(exists=True, dimension=1536, not_ready_polls=0)).query("u1", [0.0] * 384))
    with pytest.raises(PineconeError, match="not set"):
        PineconeStore(Settings(pinecone_api_key=""), 384)


# ---------- API: upload indexes, delete removes vectors ----------
def test_upload_indexes_and_delete_removes_vectors(memory_repo):
    svc, store = _service()
    app.state.retrieval = svc
    try:
        client = TestClient(app)
        r = client.post("/api/documents/upload", data={"kind": "cv"},
                        files={"file": ("cv.docx", make_docx(CV.splitlines()), "application/octet-stream")})
        doc_id = r.json()["id"]
        for _ in range(50):
            if store.data.get("dev-user"):
                break
            time.sleep(0.02)
        assert any(i.startswith(f"{doc_id}#") for i in store.data["dev-user"])
        assert client.delete(f"/api/documents/{doc_id}").status_code == 204
        for _ in range(50):
            if not any(i.startswith(f"{doc_id}#") for i in store.data["dev-user"]):
                break
            time.sleep(0.02)
        assert not any(i.startswith(f"{doc_id}#") for i in store.data["dev-user"])
    finally:
        app.state.retrieval = None


# ---------- live session: retrieval, long CV, slow retrieval, follow-ups, fact check ----------
def _run_session(monkeypatch, memory_repo, script, cv_text=CV, delay=0.0, tokens=None):
    svc, store = _service(delay=delay)
    cv = asyncio.run(memory_repo.create("dev-user", "cv", "Daniel", cv_text))
    jd = asyncio.run(memory_repo.create("dev-user", "jd", "Brightwater", JD))
    asyncio.run(svc.index_document(cv))
    asyncio.run(svc.index_document(jd))
    llm = FakeLLM(tokens=tokens or ["**Answer.**"])
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({"interviewer": script}))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    app.state.retrieval = svc
    msgs = []
    try:
        with TestClient(app).websocket_connect("/ws/session") as ws:
            ws.send_json({"type": "start", "cv_id": cv.id, "jd_id": jd.id, "session_type": "behavioral"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(bytes([0]) + b"\x00" * 3200)
            done = 0
            expected = sum(1 for t, final in script if "?" in t and final)
            while done < expected:
                m = ws.receive_json()
                msgs.append(m)
                done += m["type"] == "answer_done"
            ws.send_json({"type": "end"})
    finally:
        app.state.retrieval = None
    return msgs, llm, store


def test_retrieved_passages_are_injected_into_the_answer_prompt(monkeypatch, memory_repo):
    msgs, llm, store = _run_session(monkeypatch, memory_repo,
                                    [("Tell me about a time you convinced someone to change their mind?", True)])
    system = llm.chat_calls[0][0].content
    assert "Most relevant CV / job description passages" in system
    highlights = system.split("Most relevant CV / job description passages")[1].split("Candidate CV")[0]
    assert "$2M" in highlights
    assert "Candidate CV:\n" in system  # short CV: still included in full
    assert len(store.queries) == 1 and len(store.queries[0]["doc_ids"]) == 2  # CV and JD only
    done = next(m for m in msgs if m["type"] == "answer_done")
    assert done["timings"]["retrieval_ms"] is not None


def test_long_cv_sends_only_retrieved_passages(monkeypatch, memory_repo):
    long_cv = CV + "\n\nProjects\n" + "\n".join(f"- Filler project {i} about warehouse layout." for i in range(300))
    _, llm, _ = _run_session(monkeypatch, memory_repo, [("Tell me about a time you persuaded someone?", True)],
                             cv_text=long_cv)
    system = llm.chat_calls[0][0].content
    assert "beginning only" in system and "Filler project 250" not in system
    assert len(system) < 9000


def test_slow_retrieval_does_not_block_the_answer(monkeypatch, memory_repo):
    monkeypatch.setattr(copilot_session, "RETRIEVE_WAIT_S", 0.05)
    msgs, llm, _ = _run_session(monkeypatch, memory_repo, [("Why do you want to join our firm?", True)], delay=2)
    assert "Most relevant" not in llm.chat_calls[0][0].content
    assert next(m for m in msgs if m["type"] == "answer_done")["stopped"] is False


def test_unverified_numbers_reported_on_answer(monkeypatch, memory_repo):
    msgs, _, _ = _run_session(monkeypatch, memory_repo, [("Tell me about a difficult problem?", True)],
                              tokens=["**Cut cost by 12%** ", "and saved $15K."])
    assert next(m for m in msgs if m["type"] == "answer_done")["unverified"] == ["$15K"]


def test_narrowed_follow_up_in_same_turn_replaces_question(monkeypatch, memory_repo):
    script = [
        ("Could you dive into your ability to persuade people? Maybe tell me", False),
        ("Could you dive into your ability to persuade people? Maybe tell me about a time you convinced "
         "someone to change their mind? Yeah so", False),
    ]
    svc, _ = _service()
    llm = FakeLLM()

    async def detect(messages, tier="fast", max_tokens=200):
        latest = messages[-1].content.split("Latest interviewer turn:\n", 1)[-1]
        q = "Tell me about a time you convinced someone to change their mind?" if "convinced" in latest \
            else "Could you dive into your ability to persuade people?"
        return {"is_question": True, "question": q, "category": "behavioral"}
    llm.complete_json = detect
    monkeypatch.setattr(copilot_session, "get_stt", lambda s: FakeSTT({"interviewer": script}))
    monkeypatch.setattr(copilot_session, "get_llm", lambda s: llm)
    monkeypatch.setattr(copilot_session, "PARTIAL_CHECK_INTERVAL_S", 0)
    with TestClient(app).websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start"})
        ws.receive_json()
        ws.send_bytes(bytes([0]) + b"\x00" * 3200)
        questions = []
        while len(questions) < 2:
            m = ws.receive_json()
            if m["type"] == "question":
                questions.append(m)
        ws.send_json({"type": "end"})
    assert "replaces" not in questions[0]
    assert questions[1]["replaces"] == questions[0]["id"]


def test_answer_prompt_rules():
    from app.copilot.prompts import ANSWER_SYSTEM, DETECT_SYSTEM
    assert "NOT STAR" in ANSWER_SYSTEM and "why this company / role" in ANSWER_SYSTEM
    assert "outcomes or benefits the CV does not state" in ANSWER_SYSTEM
    assert "narrows or rephrases it in the same breath" in DETECT_SYSTEM


# ---------- framework mode for setbacks / weaknesses / salary ----------


@pytest.mark.parametrize("question,topic", [
    ("Tell me about a time you failed.", "setback"),
    ("Tell me about a mistake you made at work and what you learned.", "setback"),
    ("Describe a project that went wrong.", "setback"),
    ("What is your biggest weakness?", "weakness"),
    ("What areas for improvement have managers pointed out?", "weakness"),
    ("What are your salary expectations?", "salary"),
    ("What salary range are you looking for?", "salary"),
    ("What do you expect in terms of compensation?", "salary"),
    # must stay in normal, CV-grounded mode:
    ("Tell me about a time you solved a difficult problem.", None),
    ("How do you handle conflict with colleagues?", None),
    ("How would you rate your Python skills?", None),
    ("What are your key strengths?", None),
    ("Why do you want to join our firm?", None),
])
def test_framework_routing(question, topic):
    assert framework_topic(question, CV) == topic


def test_framework_mode_gets_no_cv_facts():
    system = answer_system_for("Tell me about a time you failed.", CV, JD, "behavioral", ["Northwind: 40 suppliers"])
    assert "Northwind" not in system and "40 suppliers" not in system and "[placeholders]" in system
    salary = answer_system_for("What are your salary expectations?", CV, JD, "mixed", [])
    assert "Never write any salary figure" in salary and "Brightwater" not in salary


def test_real_cv_setback_uses_normal_mode():
    cv = CV + "\nLessons learned: a vendor migration I led failed; I learned to pilot first."
    assert framework_topic("Tell me about a time you failed.", cv) is None
    assert "Candidate CV:" in answer_system_for("Tell me about a time you failed.", cv, None, "mixed", [])
