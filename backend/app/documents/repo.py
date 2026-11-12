"""Storage for CVs and job descriptions. Every query is scoped by user_id."""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import asyncpg

Kind = Literal["cv", "jd"]

@dataclass
class Document:
    id: str
    user_id: str
    kind: Kind
    title: str
    text: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def summary(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "chars": len(self.text),
            "preview": self.text[:200],
            "created_at": self.created_at.isoformat(),
        }


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


class DocumentRepo(ABC):
    @abstractmethod
    async def create(self, user_id: str, kind: Kind, title: str, text: str) -> Document: ...

    @abstractmethod
    async def list(self, user_id: str, kind: Kind | None = None) -> list[Document]: ...

    @abstractmethod
    async def get(self, user_id: str, doc_id: str) -> Document | None: ...

    @abstractmethod
    async def delete(self, user_id: str, doc_id: str) -> bool: ...

    async def close(self) -> None:  # noqa: B027  (optional hook)
        pass


class PostgresDocumentRepo(DocumentRepo):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresDocumentRepo:
        """Own pool, schema migrated (used by tests and scripts; the server shares one pool)."""
        from app.db.migrations import connect
        return cls(await connect(dsn))

    @staticmethod
    def _row(r: asyncpg.Record) -> Document:
        return Document(str(r["id"]), r["user_id"], r["kind"], r["title"], r["text"], r["created_at"])

    async def create(self, user_id: str, kind: Kind, title: str, text: str) -> Document:
        r = await self.pool.fetchrow(
            "INSERT INTO documents (user_id, kind, title, text) VALUES ($1, $2, $3, $4) "
            "RETURNING id, user_id, kind, title, text, created_at",
            user_id, kind, title, text,
        )
        return self._row(r)

    async def list(self, user_id: str, kind: Kind | None = None) -> list[Document]:
        rows = await self.pool.fetch(
            "SELECT id, user_id, kind, title, text, created_at FROM documents "
            "WHERE user_id = $1 AND ($2::text IS NULL OR kind = $2) ORDER BY created_at DESC",
            user_id, kind,
        )
        return [self._row(r) for r in rows]

    async def get(self, user_id: str, doc_id: str) -> Document | None:
        if not _valid_uuid(doc_id):
            return None
        r = await self.pool.fetchrow(
            "SELECT id, user_id, kind, title, text, created_at FROM documents WHERE user_id = $1 AND id = $2",
            user_id, uuid.UUID(doc_id),
        )
        return self._row(r) if r else None

    async def delete(self, user_id: str, doc_id: str) -> bool:
        if not _valid_uuid(doc_id):
            return False
        result = await self.pool.execute(
            "DELETE FROM documents WHERE user_id = $1 AND id = $2", user_id, uuid.UUID(doc_id)
        )
        return result.endswith(" 1")

    async def close(self) -> None:
        await self.pool.close()


class InMemoryDocumentRepo(DocumentRepo):
    """Used by unit tests."""

    def __init__(self):
        self.docs: dict[str, Document] = {}

    async def create(self, user_id: str, kind: Kind, title: str, text: str) -> Document:
        doc = Document(str(uuid.uuid4()), user_id, kind, title, text)
        self.docs[doc.id] = doc
        return doc

    async def list(self, user_id: str, kind: Kind | None = None) -> list[Document]:
        docs = [d for d in self.docs.values() if d.user_id == user_id and (kind is None or d.kind == kind)]
        return sorted(docs, key=lambda d: d.created_at, reverse=True)

    async def get(self, user_id: str, doc_id: str) -> Document | None:
        d = self.docs.get(doc_id)
        return d if d and d.user_id == user_id else None

    async def delete(self, user_id: str, doc_id: str) -> bool:
        d = self.docs.get(doc_id)
        if d and d.user_id == user_id:
            del self.docs[doc_id]
            return True
        return False
