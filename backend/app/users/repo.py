"""User accounts storage."""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import asyncpg


class EmailTaken(Exception):
    pass


@dataclass
class User:
    id: str
    email: str
    name: str = ""
    password_hash: str | None = None
    google_sub: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    plan: str = "free"
    stripe_customer_id: str | None = None
    subscription_id: str | None = None
    subscription_status: str | None = None
    billing_event_at: datetime | None = None

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "plan": self.plan,
                "has_password": self.password_hash is not None, "google": self.google_sub is not None,
                "created_at": self.created_at.isoformat()}


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False


class UserRepo(ABC):
    @abstractmethod
    async def create(self, email: str, name: str, password_hash: str | None) -> User: ...

    @abstractmethod
    async def get(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def upsert_google(self, email: str, name: str, google_sub: str) -> User: ...

    @abstractmethod
    async def delete(self, user_id: str) -> bool: ...

    @abstractmethod
    async def set_plan(self, user_id: str, plan: str) -> None: ...

    # ---------- billing ----------
    @abstractmethod
    async def get_by_customer(self, customer_id: str) -> User | None: ...

    @abstractmethod
    async def set_customer(self, user_id: str, customer_id: str) -> str:
        """Record the Stripe customer if the user has none yet; returns the one that is stored.
        (Two clicks at once must not leave the account pointing at two customers.)"""

    @abstractmethod
    async def apply_subscription(self, user_id: str, *, plan: str, subscription_id: str | None,
                                 status: str | None, event_at: datetime) -> bool:
        """Set plan and subscription state, unless a newer Stripe event was already applied.
        Returns False when the event was older and therefore ignored."""


_COLS = ("id, email, name, password_hash, google_sub, created_at, plan, "
         "stripe_customer_id, subscription_id, subscription_status, billing_event_at")


class PostgresUserRepo(UserRepo):
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @staticmethod
    def _row(r) -> User:
        return User(str(r["id"]), r["email"], r["name"], r["password_hash"], r["google_sub"], r["created_at"],
                    r["plan"], r["stripe_customer_id"], r["subscription_id"], r["subscription_status"],
                    r["billing_event_at"])

    async def create(self, email: str, name: str, password_hash: str | None) -> User:
        try:
            r = await self.pool.fetchrow(
                f"INSERT INTO users (email, name, password_hash) VALUES ($1, $2, $3) RETURNING {_COLS}",
                normalize_email(email), name.strip(), password_hash,
            )
        except asyncpg.UniqueViolationError as exc:
            raise EmailTaken() from exc
        return self._row(r)

    async def get(self, user_id: str) -> User | None:
        if not _valid_uuid(user_id):
            return None
        r = await self.pool.fetchrow(f"SELECT {_COLS} FROM users WHERE id = $1", uuid.UUID(user_id))
        return self._row(r) if r else None

    async def get_by_email(self, email: str) -> User | None:
        r = await self.pool.fetchrow(f"SELECT {_COLS} FROM users WHERE email = $1", normalize_email(email))
        return self._row(r) if r else None

    async def upsert_google(self, email: str, name: str, google_sub: str) -> User:
        # Same Google account -> same user. A verified Google email matching an existing
        # email/password account links to it, so the person keeps one account and one history.
        r = await self.pool.fetchrow(
            f"""INSERT INTO users (email, name, google_sub) VALUES ($1, $2, $3)
                ON CONFLICT (email) DO UPDATE
                  SET google_sub = COALESCE(users.google_sub, EXCLUDED.google_sub),
                      name = CASE WHEN users.name = '' THEN EXCLUDED.name ELSE users.name END
                RETURNING {_COLS}""",
            normalize_email(email), name.strip(), google_sub,
        )
        return self._row(r)

    async def delete(self, user_id: str) -> bool:
        if not _valid_uuid(user_id):
            return False
        return (await self.pool.execute("DELETE FROM users WHERE id = $1", uuid.UUID(user_id))).endswith(" 1")

    async def set_plan(self, user_id: str, plan: str) -> None:
        await self.pool.execute("UPDATE users SET plan = $2 WHERE id = $1", uuid.UUID(user_id), plan)

    async def get_by_customer(self, customer_id: str) -> User | None:
        r = await self.pool.fetchrow(f"SELECT {_COLS} FROM users WHERE stripe_customer_id = $1", customer_id)
        return self._row(r) if r else None

    async def set_customer(self, user_id: str, customer_id: str) -> str:
        r = await self.pool.fetchrow(
            """UPDATE users SET stripe_customer_id = COALESCE(stripe_customer_id, $2)
               WHERE id = $1 RETURNING stripe_customer_id""",
            uuid.UUID(user_id), customer_id,
        )
        return r["stripe_customer_id"] if r else customer_id

    async def apply_subscription(self, user_id: str, *, plan: str, subscription_id: str | None,
                                 status: str | None, event_at: datetime) -> bool:
        result = await self.pool.execute(
            """UPDATE users SET plan = $2, subscription_id = $3, subscription_status = $4, billing_event_at = $5
               WHERE id = $1 AND (billing_event_at IS NULL OR billing_event_at <= $5)""",
            uuid.UUID(user_id), plan, subscription_id, status, event_at,
        )
        return result.endswith(" 1")


class InMemoryUserRepo(UserRepo):
    """Used by unit tests."""

    def __init__(self):
        self.users: dict[str, User] = {}

    async def create(self, email: str, name: str, password_hash: str | None) -> User:
        if await self.get_by_email(email):
            raise EmailTaken()
        u = User(str(uuid.uuid4()), normalize_email(email), name.strip(), password_hash)
        self.users[u.id] = u
        return u

    async def get(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def get_by_email(self, email: str) -> User | None:
        e = normalize_email(email)
        return next((u for u in self.users.values() if u.email == e), None)

    async def upsert_google(self, email: str, name: str, google_sub: str) -> User:
        u = await self.get_by_email(email)
        if u is None:
            u = User(str(uuid.uuid4()), normalize_email(email), name.strip(), None, google_sub)
            self.users[u.id] = u
        elif u.google_sub is None:
            u.google_sub = google_sub
        return u

    async def delete(self, user_id: str) -> bool:
        return self.users.pop(user_id, None) is not None

    async def set_plan(self, user_id: str, plan: str) -> None:
        if user_id in self.users:
            self.users[user_id].plan = plan

    async def get_by_customer(self, customer_id: str) -> User | None:
        return next((u for u in self.users.values() if u.stripe_customer_id == customer_id), None)

    async def set_customer(self, user_id: str, customer_id: str) -> str:
        u = self.users.get(user_id)
        if u is None:
            return customer_id
        u.stripe_customer_id = u.stripe_customer_id or customer_id
        return u.stripe_customer_id

    async def apply_subscription(self, user_id: str, *, plan: str, subscription_id: str | None,
                                 status: str | None, event_at: datetime) -> bool:
        u = self.users.get(user_id)
        if u is None or (u.billing_event_at is not None and u.billing_event_at > event_at):
            return False
        u.plan, u.subscription_id, u.subscription_status, u.billing_event_at = plan, subscription_id, status, event_at
        return True
