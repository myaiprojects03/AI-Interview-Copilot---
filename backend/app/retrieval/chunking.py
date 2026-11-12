"""Split CV / JD text into retrieval chunks.

CVs are short and structured, so chunks follow the document's own structure: a new chunk
starts at each heading, and long sections are split at line boundaries. Each chunk is
prefixed with its section heading so a retrieved bullet keeps its context
(e.g. "Operations Analyst — Northwind Energy: built a long-list of 40 suppliers...").
"""
from __future__ import annotations

import re

MAX_CHARS = 700

_BULLET = re.compile(r"^[-•*▪◦·]\s*")


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 80 or _BULLET.match(line):
        return False
    if line.endswith((".", ",", ";")):
        return False
    # Short line without sentence punctuation: "Experience", "Operations Analyst — Northwind (2022 – present)"
    return len(line.split()) <= 12


def _headings(lines: list[str]) -> list[bool]:
    """A one-to-three-word line ("Education", "Skills") is always a heading. A longer heading-like
    line ("Operations Analyst — Northwind (2022)") is a heading only when real content follows it;
    otherwise it is content itself ("BSc Industrial Engineering — Riverside University (2021)")."""
    looks = [_looks_like_heading(ln) for ln in lines]
    result = []
    for i, ln in enumerate(lines):
        if not looks[i]:
            result.append(False)
        elif len(ln.split()) <= 3:
            result.append(True)
        else:
            result.append(i + 1 < len(lines) and not looks[i + 1])
    return result


def chunk_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    is_heading = _headings(lines)
    chunks: list[str] = []
    heading = ""
    body: list[str] = []

    def flush() -> None:
        content = " ".join(body).strip()
        if content:
            chunks.append(f"{heading}: {content}" if heading else content)
        body.clear()

    for line, heading_line in zip(lines, is_heading, strict=True):
        if heading_line:
            flush()
            heading = line.lstrip("#").strip()
            continue
        line = _BULLET.sub("", line)
        if body and len(" ".join(body)) + len(line) + 1 > max_chars:
            flush()
        # A single line longer than the limit is split on sentence boundaries.
        while len(line) > max_chars:
            cut = line.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            body.append(line[:cut].strip())
            flush()
            line = line[cut:].strip()
        body.append(line)
    flush()
    # Headings with no body (e.g. a document that is only a list of headings) are still searchable.
    return chunks or ([text.strip()[:max_chars]] if text.strip() else [])
