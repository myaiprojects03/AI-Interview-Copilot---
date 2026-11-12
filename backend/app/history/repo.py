"""Interview history: sessions, their transcript lines and the answers shown. Scoped by user."""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg


@dataclass
class SessionSummary:
    id: str
    started_at: datetime
    ended_at: datetime | None
    seconds_used: int
    session_type: str
    cv_title: str
    jd_title: str | None
    questions: int = 0

    def public(self) -> dict:
        return {"id": self.id, "started_at": self.started_at.isoformat(),
                "ended_at": self.ended_at.isoformat() if self.ended_at else None,
                "seconds_used": self.seconds_used, "session_type": self.session_type,
                "cv_title": self.cv_title, "jd_title": self.jd_title, "questions": self.questions}


@dataclass
class SessionDetail:
    summary: SessionSummary
    lines: list[dict] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        return {**self.summary.public(), "lines": self.lines, "answers": self.answers}


@dataclass
class ResumedSession:
    """What a reconnecting connection needs to carry on where the dropped one stopped."""
    next_seq: int                                   # transcript line number to continue from
    recent_lines: list[tuple[str, str]]             # (speaker, text), oldest first: model context
    questions: list[str]                            # already answered: not detected again


@dataclass
class LatencyStats:
    answers: int
    metrics: dict[str, dict[str, float | None]]     # metric -> {"p50", "p95", "max"}
    under_2s: float | None                          # share of answers with first words under 2 s


LATENCY_METRICS = ("detect_ms", "retrieval_ms", "first_token_ms", "total_ms")


def _percentile(values: list[int], q: float) -> float | None:
    """Linear interpolation, same as Postgres percentile_cont."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


class HistoryRepo(ABC):
    @abstractmethod
    async def create_session(self, user_id: str, session_type: str, cv_title: str, jd_title: str | None,
                             connection_id: str | None = None) -> str: ...

    @abstractmethod
    async def resume_session(self, user_id: str, session_id: str, connection_id: str, within_s: int,
                             context_lines: int) -> ResumedSession | None:
        """Take over the user's session if it is still open or ended less than within_s ago."""

    @abstractmethod
    async def latency_stats(self, since: datetime) -> LatencyStats: ...

    @abstractmethod
    async def add_line(self, session_id: str, seq: int, speaker: str, text: str) -> None: ...

    @abstractmethod
    async def save_answer(self, session_id: str, question_id: str, question: str, category: str, answer: str,
                          unverified: list[str], timings: dict) -> None: ...

    @abstractmethod
    async def delete_answer(self, session_id: str, question_id: str) -> None: ...

    @abstractmethod
    async def add_seconds(self, session_id: str, seconds: int) -> None: ...

    @abstractmethod
    async def end_session(self, session_id: str, connection_id: str | None = None) -> None:
        """Mark ended, unless another connection has since resumed it."""

    @abstractmethod
    async def used_seconds_this_month(self, user_id: str) -> int: ...

    @abstractmethod
    async def list_sessions(self, user_id: str, limit: int = 50) -> list[SessionSummary]: ...

    @abstractmethod
    async def get_session(self, user_id: str, session_id: str) -> SessionDetail | None: ...

    @abstractmethod
    async def delete_session(self, user_id: str, session_id: str) -> bool: ...

    @abstractmethod
    async def delete_user_history(self, user_id: str) -> None: ...


class PostgresHistoryRepo(HistoryRepo):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_session(self, user_id: str, session_type: str, cv_title: str, jd_title: str | None,
                             connection_id: str | None = None) -> str:
        r = await self.pool.fetchrow(
            "INSERT INTO interview_sessions (user_id, session_type, cv_title, jd_title, connection_id) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            uuid.UUID(user_id), session_type, cv_title, jd_title, connection_id)
        return str(r["id"])

    async def resume_session(self, user_id: str, session_id: str, connection_id: str, within_s: int,
                             context_lines: int) -> ResumedSession | None:
        if not _valid_uuid(session_id):
            return None
        sid = uuid.UUID(session_id)
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """UPDATE interview_sessions SET ended_at = NULL, connection_id = $3
                   WHERE id = $1 AND user_id = $2
                     AND (ended_at IS NULL OR ended_at > now() - make_interval(secs => $4))
                   RETURNING id""", sid, uuid.UUID(user_id), connection_id, float(within_s))
            if row is None:
                return None
            next_seq = await conn.fetchval(
                "SELECT COALESCE(MAX(seq) + 1, 0) FROM session_lines WHERE session_id = $1", sid)
            lines = await conn.fetch(
                "SELECT speaker, text FROM session_lines WHERE session_id = $1 ORDER BY seq DESC LIMIT $2",
                sid, context_lines)
            questions = await conn.fetch(
                "SELECT question FROM session_answers WHERE session_id = $1 ORDER BY created_at", sid)
        return ResumedSession(int(next_seq), [(r["speaker"], r["text"]) for r in reversed(lines)],
                              [r["question"] for r in questions])

    async def latency_stats(self, since: datetime) -> LatencyStats:
        parts = ", ".join(
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY (timings->>'{m}')::float) AS {m}_p50, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY (timings->>'{m}')::float) AS {m}_p95, "
            f"MAX((timings->>'{m}')::float) AS {m}_max"
            for m in LATENCY_METRICS)
        r = await self.pool.fetchrow(
            f"""SELECT COUNT(*) AS answers,
                       AVG(CASE WHEN (timings->>'first_token_ms')::float < 2000 THEN 1.0 ELSE 0.0 END)
                         FILTER (WHERE timings->>'first_token_ms' IS NOT NULL) AS under_2s,
                       {parts}
                FROM session_answers WHERE created_at >= $1""", since)
        return LatencyStats(
            int(r["answers"]),
            {m: {k: (float(r[f"{m}_{k}"]) if r[f"{m}_{k}"] is not None else None) for k in ("p50", "p95", "max")}
             for m in LATENCY_METRICS},
            float(r["under_2s"]) if r["under_2s"] is not None else None)

    async def add_line(self, session_id: str, seq: int, speaker: str, text: str) -> None:
        await self.pool.execute(
            "INSERT INTO session_lines (session_id, seq, speaker, text) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT DO NOTHING", uuid.UUID(session_id), seq, speaker, text)

    async def save_answer(self, session_id: str, question_id: str, question: str, category: str, answer: str,
                          unverified: list[str], timings: dict) -> None:
        await self.pool.execute(
            """INSERT INTO session_answers (session_id, question_id, question, category, answer, unverified, timings)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb)
               ON CONFLICT (session_id, question_id) DO UPDATE
                 SET answer = EXCLUDED.answer, unverified = EXCLUDED.unverified, timings = EXCLUDED.timings""",
            uuid.UUID(session_id), question_id, question, category, answer, json.dumps(unverified),
            json.dumps(timings))

    async def delete_answer(self, session_id: str, question_id: str) -> None:
        await self.pool.execute("DELETE FROM session_answers WHERE session_id = $1 AND question_id = $2",
                                uuid.UUID(session_id), question_id)

    async def add_seconds(self, session_id: str, seconds: int) -> None:
        await self.pool.execute("UPDATE interview_sessions SET seconds_used = seconds_used + $2 WHERE id = $1",
                                uuid.UUID(session_id), seconds)

    async def end_session(self, session_id: str, connection_id: str | None = None) -> None:
        await self.pool.execute(
            "UPDATE interview_sessions SET ended_at = now() WHERE id = $1 AND ended_at IS NULL "
            "AND ($2::text IS NULL OR connection_id IS NULL OR connection_id = $2)",
            uuid.UUID(session_id), connection_id)

    async def used_seconds_this_month(self, user_id: str) -> int:
        v = await self.pool.fetchval(
            "SELECT COALESCE(SUM(seconds_used), 0) FROM interview_sessions WHERE user_id = $1 AND started_at >= $2",
            uuid.UUID(user_id), _month_start())
        return int(v)

    @staticmethod
    def _summary(r) -> SessionSummary:
        return SessionSummary(str(r["id"]), r["started_at"], r["ended_at"], r["seconds_used"], r["session_type"],
                              r["cv_title"], r["jd_title"], r["questions"])

    _SUMMARY_SQL = """SELECT s.id, s.started_at, s.ended_at, s.seconds_used, s.session_type, s.cv_title, s.jd_title,
                             (SELECT COUNT(*) FROM session_answers a WHERE a.session_id = s.id) AS questions
                      FROM interview_sessions s"""

    async def list_sessions(self, user_id: str, limit: int = 50) -> list[SessionSummary]:
        rows = await self.pool.fetch(f"{self._SUMMARY_SQL} WHERE s.user_id = $1 ORDER BY s.started_at DESC LIMIT $2",
                                     uuid.UUID(user_id), limit)
        return [self._summary(r) for r in rows]

    async def get_session(self, user_id: str, session_id: str) -> SessionDetail | None:
        if not _valid_uuid(session_id):
            return None
        r = await self.pool.fetchrow(f"{self._SUMMARY_SQL} WHERE s.user_id = $1 AND s.id = $2",
                                     uuid.UUID(user_id), uuid.UUID(session_id))
        if r is None:
            return None
        lines = await self.pool.fetch("SELECT seq, speaker, text FROM session_lines WHERE session_id = $1 ORDER BY seq",
                                      r["id"])
        answers = await self.pool.fetch(
            "SELECT question_id, question, category, answer, unverified, timings, created_at FROM session_answers "
            "WHERE session_id = $1 ORDER BY created_at", r["id"])
        return SessionDetail(
            self._summary(r),
            [{"seq": x["seq"], "speaker": x["speaker"], "text": x["text"]} for x in lines],
            [{"question_id": a["question_id"], "question": a["question"], "category": a["category"],
              "answer": a["answer"], "unverified": json.loads(a["unverified"]), "timings": json.loads(a["timings"]),
              "created_at": a["created_at"].isoformat()} for a in answers])

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        if not _valid_uuid(session_id):
            return False
        res = await self.pool.execute("DELETE FROM interview_sessions WHERE user_id = $1 AND id = $2",
                                      uuid.UUID(user_id), uuid.UUID(session_id))
        return res.endswith(" 1")

    async def delete_user_history(self, user_id: str) -> None:
        await self.pool.execute("DELETE FROM interview_sessions WHERE user_id = $1", uuid.UUID(user_id))


class InMemoryHistoryRepo(HistoryRepo):
    """Used by unit tests."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}

    async def create_session(self, user_id, session_type, cv_title, jd_title, connection_id=None) -> str:
        sid = str(uuid.uuid4())
        self.sessions[sid] = {"user_id": user_id, "summary": SessionSummary(
            sid, datetime.now(UTC), None, 0, session_type, cv_title, jd_title), "lines": {}, "answers": {},
            "connection_id": connection_id}
        return sid

    async def resume_session(self, user_id, session_id, connection_id, within_s, context_lines):
        s = self.sessions.get(session_id)
        if not s or s["user_id"] != user_id:
            return None
        ended = s["summary"].ended_at
        if ended is not None and (datetime.now(UTC) - ended).total_seconds() > within_s:
            return None
        s["summary"].ended_at = None
        s["connection_id"] = connection_id
        lines = sorted(s["lines"].values(), key=lambda x: x["seq"])
        return ResumedSession((lines[-1]["seq"] + 1) if lines else 0,
                              [(x["speaker"], x["text"]) for x in lines[-context_lines:]],
                              [a["question"] for a in s["answers"].values()])

    async def latency_stats(self, since):
        answers = [a for s in self.sessions.values() for a in s["answers"].values()
                   if datetime.fromisoformat(a["created_at"]) >= since]
        metrics: dict[str, dict[str, float | None]] = {}
        for m in LATENCY_METRICS:
            vals = [a["timings"][m] for a in answers if a["timings"].get(m) is not None]
            metrics[m] = {"p50": _percentile(vals, 0.5), "p95": _percentile(vals, 0.95),
                          "max": float(max(vals)) if vals else None}
        firsts = [a["timings"]["first_token_ms"] for a in answers if a["timings"].get("first_token_ms") is not None]
        return LatencyStats(len(answers), metrics,
                            sum(1 for v in firsts if v < 2000) / len(firsts) if firsts else None)

    async def add_line(self, session_id, seq, speaker, text) -> None:
        self.sessions[session_id]["lines"].setdefault(seq, {"seq": seq, "speaker": speaker, "text": text})

    async def save_answer(self, session_id, question_id, question, category, answer, unverified, timings) -> None:
        self.sessions[session_id]["answers"][question_id] = {
            "question_id": question_id, "question": question, "category": category, "answer": answer,
            "unverified": unverified, "timings": timings, "created_at": datetime.now(UTC).isoformat()}

    async def delete_answer(self, session_id, question_id) -> None:
        self.sessions[session_id]["answers"].pop(question_id, None)

    async def add_seconds(self, session_id, seconds) -> None:
        self.sessions[session_id]["summary"].seconds_used += seconds

    async def end_session(self, session_id, connection_id=None) -> None:
        owner = self.sessions[session_id].get("connection_id")
        if connection_id is not None and owner is not None and owner != connection_id:
            return
        s = self.sessions[session_id]["summary"]
        s.ended_at = s.ended_at or datetime.now(UTC)

    async def used_seconds_this_month(self, user_id) -> int:
        start = _month_start()
        return sum(s["summary"].seconds_used for s in self.sessions.values()
                   if s["user_id"] == user_id and s["summary"].started_at >= start)

    def _summary(self, sid) -> SessionSummary:
        s = self.sessions[sid]
        s["summary"].questions = len(s["answers"])
        return s["summary"]

    async def list_sessions(self, user_id, limit=50) -> list[SessionSummary]:
        own = [self._summary(k) for k, v in self.sessions.items() if v["user_id"] == user_id]
        return sorted(own, key=lambda s: s.started_at, reverse=True)[:limit]

    async def get_session(self, user_id, session_id) -> SessionDetail | None:
        s = self.sessions.get(session_id)
        if not s or s["user_id"] != user_id:
            return None
        return SessionDetail(self._summary(session_id), sorted(s["lines"].values(), key=lambda x: x["seq"]),
                             list(s["answers"].values()))

    async def delete_session(self, user_id, session_id) -> bool:
        s = self.sessions.get(session_id)
        if not s or s["user_id"] != user_id:
            return False
        del self.sessions[session_id]
        return True

    async def delete_user_history(self, user_id) -> None:
        for k in [k for k, v in self.sessions.items() if v["user_id"] == user_id]:
            del self.sessions[k]
