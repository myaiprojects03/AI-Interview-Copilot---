"""Sign-up, sign-in, Google hand-off, profile and account deletion."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth import current_user_id, jwt_secret
from app.config import get_settings, is_admin
from app.shared.store import SharedStore, get_store
from app.users.repo import EmailTaken, UserRepo
from app.users.security import DUMMY_HASH, create_access_token, hash_password, password_problem, verify_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Failed sign-ins per email+IP, and sign-ups per IP. Kept in the shared store (Redis), so the
# limits hold across every server instance.
MAX_FAILED = 8
WINDOW_S = 15 * 60
MAX_SIGNUPS = 10
SIGNUP_WINDOW_S = 60 * 60


def get_users(request: Request) -> UserRepo:
    users = getattr(request.app.state, "users", None)
    if users is None:
        raise HTTPException(503, "Database is not available. Check that Docker (Postgres) is running.")
    return users


def _token_response(user) -> dict:
    s = get_settings()
    return {"user": user.public(),
            "access_token": create_access_token(user.id, jwt_secret(), s.access_token_days),
            "expires_in_days": s.access_token_days}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _key(kind: str, *parts: str) -> str:
    # Hashed so emails are not stored in Redis in clear text.
    return f"{kind}:" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


async def _over_limit(store: SharedStore, key: str, limit: int) -> bool:
    try:
        return await store.count(key) >= limit
    except Exception:
        # A Redis outage must not lock everyone out: allow the request and log it.
        log.exception("rate limit check failed; allowing the request")
        return False


async def _record(store: SharedStore, key: str, window_s: int) -> None:
    try:
        await store.hit(key, window_s)
    except Exception:
        log.exception("could not record a rate-limit event")


class RegisterIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    name: str = Field(default="", max_length=100)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class GoogleIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=100)
    google_sub: str = Field(min_length=1, max_length=255)


@router.post("/auth/register", status_code=201)
async def register(body: RegisterIn, request: Request, users: UserRepo = Depends(get_users),
                   store: SharedStore = Depends(get_store)) -> dict:
    signup_key = _key("signups", _client_ip(request))
    if await _over_limit(store, signup_key, MAX_SIGNUPS):
        raise HTTPException(429, "Too many new accounts from this network. Try again in an hour.")
    if not _EMAIL.match(body.email.strip()):
        raise HTTPException(422, "Enter a valid email address.")
    if problem := password_problem(body.password):
        raise HTTPException(422, problem)
    try:
        user = await users.create(body.email, body.name, hash_password(body.password))
    except EmailTaken:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.") from None
    await _record(store, signup_key, SIGNUP_WINDOW_S)
    return _token_response(user)


@router.post("/auth/login")
async def login(body: LoginIn, request: Request, users: UserRepo = Depends(get_users),
                store: SharedStore = Depends(get_store)) -> dict:
    key = _key("login-failures", body.email.strip().lower(), _client_ip(request))
    if await _over_limit(store, key, MAX_FAILED):
        raise HTTPException(429, "Too many sign-in attempts. Wait 15 minutes and try again.")
    user = await users.get_by_email(body.email)
    # Always run one bcrypt check so the response time does not reveal whether the email exists.
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH) and user is not None
    if not ok:
        await _record(store, key, WINDOW_S)
        if user is not None and user.password_hash is None:
            raise HTTPException(401, "This account uses Google sign-in. Use “Continue with Google”.")
        raise HTTPException(401, "Email or password is incorrect.")
    try:
        await store.clear(key)
    except Exception:
        log.exception("could not clear sign-in failures")
    return _token_response(user)


@router.post("/auth/google")
async def google(body: GoogleIn, x_internal_secret: str = Header(default=""),
                 users: UserRepo = Depends(get_users)) -> dict:
    """Called only by the Next.js server after Google has verified the user (never by browsers)."""
    expected = get_settings().internal_api_secret
    if not expected or not hmac.compare_digest(x_internal_secret, expected):
        raise HTTPException(403, "Forbidden")
    if not _EMAIL.match(body.email.strip()):
        raise HTTPException(422, "Google did not provide a valid email.")
    return _token_response(await users.upsert_google(body.email, body.name, body.google_sub))


@router.get("/me")
async def me(user_id: str = Depends(current_user_id), users: UserRepo = Depends(get_users)) -> dict:
    user = await users.get(user_id)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return {**user.public(), "is_admin": is_admin(user.email)}


@router.delete("/me", status_code=204, response_class=Response)
async def delete_me(request: Request, user_id: str = Depends(current_user_id),
                    users: UserRepo = Depends(get_users)) -> Response:
    """Delete the account and everything stored for it: CVs, job descriptions, their search
    vectors, interview history, then the user. Any subscription is cancelled first: if that fails,
    nothing is deleted, so nobody keeps paying for an account that no longer exists."""
    billing = getattr(request.app.state, "billing", None)
    user = await users.get(user_id)
    if billing is not None and user is not None:
        try:
            await billing.cancel_for_account_deletion(user)
        except Exception as exc:
            log.exception("could not cancel the subscription of %s", user_id)
            raise HTTPException(502, "We couldn't cancel your subscription, so your account was not deleted. "
                                     "Try again in a minute.") from exc
    repo = getattr(request.app.state, "repo", None)
    history = getattr(request.app.state, "history", None)
    if history is not None:
        await history.delete_user_history(user_id)  # also removed by ON DELETE CASCADE in Postgres
    retrieval = getattr(request.app.state, "retrieval", None)
    if repo is not None:
        for doc in await repo.list(user_id):
            if retrieval is not None:
                try:
                    await retrieval.delete_document(doc)
                except Exception:
                    # Vectors are only reachable through this user's namespace, which is never
                    # queried again; log so it can be cleaned up, but do not block the deletion.
                    log.exception("could not delete vectors for document %s", doc.id)
            await repo.delete(user_id, doc.id)
    await users.delete(user_id)
    log.info("deleted account %s and its data", user_id)
    return Response(status_code=204)
