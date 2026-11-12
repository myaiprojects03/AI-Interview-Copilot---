"""Password hashing and access tokens."""
from __future__ import annotations

import time

import bcrypt
import jwt

MIN_PASSWORD = 8
MAX_PASSWORD_BYTES = 72  # bcrypt only uses the first 72 bytes; longer passwords are rejected, not truncated
ALGORITHM = "HS256"


class TokenError(Exception):
    pass


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return "Password is too long (maximum 72 bytes)."
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash or len(password.encode()) > MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# A real hash to compare against when the email does not exist, so login takes the same time
# whether or not the account exists (prevents discovering registered emails by timing).
DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def create_access_token(user_id: str, secret: str, days: int) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + days * 86400, "typ": "access"}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str) -> str:
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Your session has expired. Please sign in again.") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Please sign in again.") from exc
    if payload.get("typ") != "access":
        raise TokenError("Please sign in again.")
    return str(payload["sub"])
