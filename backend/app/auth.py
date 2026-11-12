"""Request authentication: who is calling?

HTTP requests send `Authorization: Bearer <token>`. Tokens are issued by /api/auth/login and /register.

Browsers cannot set headers on WebSockets, and a long-lived token in a URL ends up in proxy and
server logs. So the live session uses a ticket instead: the browser asks for one with its token
(POST /api/session/ticket), then connects with `?ticket=...`. A ticket works once, within
TICKET_TTL_S seconds, on any server instance (it is kept in Redis).
"""
from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request, WebSocket

from app.config import get_settings
from app.shared.store import store_of
from app.users.security import TokenError, decode_access_token

log = logging.getLogger(__name__)

TICKET_TTL_S = 30

_dev_secret: str | None = None


def jwt_secret() -> str:
    """The token signing secret. In development a random one is generated if JWT_SECRET is unset
    (everyone is signed out when the server restarts); anywhere else it must be configured."""
    global _dev_secret
    s = get_settings()
    if s.jwt_secret:
        if len(s.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters (generate one with: "
                               "python -c \"import secrets; print(secrets.token_urlsafe(32))\").")
        return s.jwt_secret
    if s.app_env != "development":
        raise RuntimeError("JWT_SECRET must be set outside development.")
    if _dev_secret is None:
        _dev_secret = secrets.token_urlsafe(32)
        log.warning("JWT_SECRET is not set: using a temporary secret; sign-ins end when the server restarts")
    return _dev_secret


async def _user_exists(app, user_id: str) -> bool:
    users = getattr(app.state, "users", None)
    return users is None or await users.get(user_id) is not None


async def user_id_from_token(app, token: str | None) -> str:
    if not token:
        raise TokenError("Please sign in.")
    user_id = decode_access_token(token, jwt_secret())
    # A deleted account's token stops working immediately, not when it expires.
    if not await _user_exists(app, user_id):
        raise TokenError("Please sign in again.")
    return user_id


async def current_user_id(request: Request) -> str:
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    try:
        return await user_id_from_token(request.app, token)
    except TokenError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


async def issue_ticket(app, user_id: str) -> str:
    ticket = secrets.token_urlsafe(32)
    await store_of(app).put(f"ws-ticket:{ticket}", user_id, TICKET_TTL_S)
    return ticket


async def websocket_user_id(ws: WebSocket) -> str:
    """Raises TokenError; the caller reports it to the client and closes the socket."""
    ticket = ws.query_params.get("ticket")
    if not ticket or len(ticket) > 100:
        raise TokenError("Please sign in.")
    user_id = await store_of(ws.app).take(f"ws-ticket:{ticket}")
    if user_id is None:
        raise TokenError("This connection link has expired. Start again.")
    if not await _user_exists(ws.app, user_id):
        raise TokenError("Please sign in again.")
    return user_id
