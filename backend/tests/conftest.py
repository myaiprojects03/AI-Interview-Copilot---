import io

import pytest
from docx import Document as DocxDocument

from app.main import app


@pytest.fixture(autouse=True)
def fresh_store():
    """Rate limits and tickets start empty in every test."""
    from app.shared.store import MemoryStore
    previous = getattr(app.state, "store", None)
    app.state.store = store = MemoryStore()
    yield store
    app.state.store = previous


@pytest.fixture
def memory_repo():
    """Attach an in-memory document store to the app for one test."""
    from app.documents.repo import InMemoryDocumentRepo
    repo = InMemoryDocumentRepo()
    previous = getattr(app.state, "repo", None)
    app.state.repo = repo
    yield repo
    app.state.repo = previous


def make_pdf(lines: list[str]) -> bytes:
    """Build a minimal valid text PDF (one page, Helvetica) without extra dependencies."""
    text_ops = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(
        "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for line in lines
    ) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> "
        "/Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text_ops)} >>\nstream\n{text_ops}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    doc = DocxDocument()
    for p in paragraphs:
        doc.add_paragraph(p)
    if table:
        t = doc.add_table(rows=len(table), cols=len(table[0]))
        for r, row in enumerate(table):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- authentication ----------
TEST_USER = "dev-user"


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: run with real token authentication (no test user override)")


@pytest.fixture(autouse=True)
def _signed_in_test_user(request, monkeypatch):
    """Most tests are about features, not sign-in: they run as a fixed signed-in user.
    Tests marked @pytest.mark.real_auth exercise the real token checks instead."""
    if request.node.get_closest_marker("real_auth"):
        yield
        return
    from app.api import session_ws
    from app.auth import current_user_id

    async def fixed_ws_user(ws):
        return TEST_USER

    app.dependency_overrides[current_user_id] = lambda: TEST_USER
    monkeypatch.setattr(session_ws, "websocket_user_id", fixed_ws_user)
    yield
    app.dependency_overrides.pop(current_user_id, None)
