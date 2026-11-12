"""Versioned database migrations, applied automatically at server start.

Each migration runs once, in order, inside a transaction. A Postgres advisory lock makes
concurrent server instances wait for each other instead of migrating twice.
To change the schema, append a new (version, sql) entry; never edit an applied one.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

log = logging.getLogger(__name__)

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
        CREATE TABLE IF NOT EXISTS documents (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL,
            kind        TEXT NOT NULL CHECK (kind IN ('cv', 'jd')),
            title       TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS documents_user_kind ON documents (user_id, kind, created_at DESC);
    """),
    (2, """
        CREATE TABLE users (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email          TEXT NOT NULL UNIQUE,          -- stored lower-case
            name           TEXT NOT NULL DEFAULT '',
            password_hash  TEXT,                          -- NULL for Google-only accounts
            google_sub     TEXT UNIQUE,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """),
    (3, """
        ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'free';

        CREATE TABLE interview_sessions (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at      TIMESTAMPTZ,
            seconds_used  INT NOT NULL DEFAULT 0,
            session_type  TEXT NOT NULL DEFAULT 'mixed',
            cv_title      TEXT NOT NULL DEFAULT '',
            jd_title      TEXT
        );
        CREATE INDEX interview_sessions_user ON interview_sessions (user_id, started_at DESC);

        CREATE TABLE session_lines (
            session_id  UUID NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            seq         INT NOT NULL,
            speaker     TEXT NOT NULL,
            text        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (session_id, seq)
        );

        CREATE TABLE session_answers (
            session_id   UUID NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
            question_id  TEXT NOT NULL,
            question     TEXT NOT NULL,
            category     TEXT NOT NULL,
            answer       TEXT NOT NULL,
            unverified   JSONB NOT NULL DEFAULT '[]',
            timings      JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (session_id, question_id)
        );
    """),
    (4, """
        -- Stripe billing. users.plan stays the one value the rest of the app reads; these columns
        -- record where it came from. billing_event_at is the creation time of the last Stripe event
        -- applied, so a late, older event can never overwrite a newer state.
        ALTER TABLE users
            ADD COLUMN stripe_customer_id  TEXT UNIQUE,
            ADD COLUMN subscription_id     TEXT,
            ADD COLUMN subscription_status TEXT,
            ADD COLUMN billing_event_at    TIMESTAMPTZ;
    """),
    (5, """
        -- Reconnect: a dropped connection can resume the same interview. connection_id names the
        -- connection that currently owns the session, so the old connection's clean-up cannot mark
        -- a resumed session as ended.
        ALTER TABLE interview_sessions ADD COLUMN connection_id TEXT;
        -- Latency report (answers in a time window).
        CREATE INDEX IF NOT EXISTS session_answers_created_idx ON session_answers (created_at);
    """),
]

_LOCK_ID = 7_314_002  # arbitrary constant for pg_advisory_lock


async def migrate(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_ID)
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for version, sql in MIGRATIONS:
                if version in done:
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
                log.info("applied database migration %d", version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)


def normalize_dsn(dsn: str) -> str:
    """Accept connection strings exactly as hosted Postgres providers (e.g. Neon) show them.

    - `channel_binding` is a libpq option asyncpg does not know; asyncpg would send it to the server
      as a setting and the connection would fail. It is removed (TLS from sslmode stays on).
    - A Neon "-pooler" host runs PgBouncer in transaction mode, which breaks the advisory lock used
      by migrations and asyncpg's prepared statements; the direct host is used instead.
    """
    parts = urlsplit(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "channel_binding"]
    netloc = parts.netloc
    if "-pooler." in netloc and "neon.tech" in netloc:
        netloc = netloc.replace("-pooler.", ".", 1)
        log.info("using the direct Neon host instead of the pooler")
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))


async def connect(dsn: str) -> asyncpg.Pool:
    # Idle connections are recycled after a minute: hosted Postgres (Neon) suspends after 5 idle
    # minutes and drops them, and a dropped connection would fail the next request.
    pool = await asyncpg.create_pool(normalize_dsn(dsn), min_size=1, max_size=10, timeout=10,
                                     max_inactive_connection_lifetime=60)
    await migrate(pool)
    return pool
