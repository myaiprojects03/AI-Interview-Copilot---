"""CV and job description endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.auth import current_user_id
from app.background import spawn
from app.documents.parsing import MAX_UPLOAD_BYTES, ParseError, extract_text, title_from, validate_text
from app.documents.repo import Document, DocumentRepo

router = APIRouter(prefix="/api/documents", tags=["documents"])

Kind = Literal["cv", "jd"]


def get_repo(request: Request) -> DocumentRepo:
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(503, "Database is not available. Check that Docker (Postgres) is running.")
    return repo


def index_in_background(request: Request, doc: Document) -> None:
    """Embed and store the document for retrieval without making the upload wait.
    If this fails, the session re-indexes the document when it starts."""
    retrieval = getattr(request.app.state, "retrieval", None)
    if retrieval is not None:
        spawn(request.app, retrieval.index_document(doc), f"index {doc.kind} {doc.id}")


class TextIn(BaseModel):
    kind: Kind
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=120)


@router.post("/upload", status_code=201)
async def upload(
    request: Request,
    kind: Kind = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(current_user_id),
    repo: DocumentRepo = Depends(get_repo),
) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        text = extract_text(file.filename or "upload.txt", data)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    doc = await repo.create(user_id, kind, title_from(file.filename or "Untitled"), text)
    index_in_background(request, doc)
    return doc.summary()


@router.post("/text", status_code=201)
async def save_text(
    request: Request,
    body: TextIn,
    user_id: str = Depends(current_user_id),
    repo: DocumentRepo = Depends(get_repo),
) -> dict:
    try:
        text = validate_text(body.text)
    except ParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    default = "Job description" if body.kind == "jd" else "CV"
    title = (body.title or "").strip() or f"{default} – {text.splitlines()[0][:60]}"
    doc = await repo.create(user_id, body.kind, title, text)
    index_in_background(request, doc)
    return doc.summary()


@router.get("")
async def list_documents(
    kind: Kind | None = None,
    user_id: str = Depends(current_user_id),
    repo: DocumentRepo = Depends(get_repo),
) -> list[dict]:
    return [d.summary() for d in await repo.list(user_id, kind)]


@router.delete("/{doc_id}", status_code=204, response_class=Response)
async def delete_document(
    request: Request,
    doc_id: str,
    user_id: str = Depends(current_user_id),
    repo: DocumentRepo = Depends(get_repo),
) -> Response:
    doc = await repo.get(user_id, doc_id)
    if doc is None or not await repo.delete(user_id, doc_id):
        raise HTTPException(404, "Document not found.")
    retrieval = getattr(request.app.state, "retrieval", None)
    if retrieval is not None:
        spawn(request.app, retrieval.delete_document(doc), f"delete vectors {doc.id}")
    return Response(status_code=204)
