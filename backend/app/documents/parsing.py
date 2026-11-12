"""Extract plain text from uploaded CVs and job descriptions (PDF, DOCX, TXT).

pypdf (BSD) and python-docx (MIT) are used instead of PyMuPDF, whose AGPL licence
conflicts with a closed-source SaaS.
"""
from __future__ import annotations

import io
import re

from docx import Document as DocxDocument
from docx.table import Table as DocxTable
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # under Vercel's 4.5 MB request limit
MAX_TEXT_CHARS = 30_000   # ~7.5k tokens; longer documents are truncated
MIN_TEXT_CHARS = 50
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class ParseError(ValueError):
    """User-facing parsing problem."""


def extension(filename: str) -> str:
    name = filename.lower()
    return name[name.rfind("."):] if "." in name else ""


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("This PDF is password-protected. Remove the password and upload again.")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise ParseError("This PDF could not be read. Try exporting it again, or upload a DOCX.") from exc


def _docx_text(data: bytes) -> str:
    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several types for corrupt files
        raise ParseError("This Word file could not be read. Save it as .docx and upload again.") from exc
    parts: list[str] = []
    # Paragraphs and tables in document order (CVs often put skills or experience in tables),
    # so a table stays under the heading it belongs to.
    for block in doc.iter_inner_content():
        if isinstance(block, DocxTable):
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(dict.fromkeys(cells)))  # merged cells repeat; dedupe
        else:
            parts.append(block.text)
    return "\n".join(parts)


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def extract_text(filename: str, data: bytes) -> str:
    ext = extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ParseError("Unsupported file type. Upload a PDF, DOCX or TXT file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ParseError("File is larger than 4 MB.")
    if ext == ".pdf":
        raw = _pdf_text(data)
    elif ext == ".docx":
        raw = _docx_text(data)
    else:
        raw = data.decode("utf-8", errors="ignore")
    return validate_text(raw)


def validate_text(raw: str) -> str:
    text = clean_text(raw)
    if len(text) < MIN_TEXT_CHARS:
        raise ParseError(
            "Almost no text was found. If this is a scanned PDF, upload a text-based PDF or DOCX instead."
        )
    return text[:MAX_TEXT_CHARS]


def title_from(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return (base[: base.rfind(".")] if "." in base else base)[:120] or "Untitled"
