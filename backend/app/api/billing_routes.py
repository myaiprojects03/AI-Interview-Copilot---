"""Billing endpoints: start checkout, open the self-service portal, receive Stripe webhooks."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import current_user_id
from app.billing.gateway import InvalidWebhook, verify_and_parse
from app.billing.service import HANDLED_EVENTS, BillingError, BillingService
from app.config import get_settings
from app.users.repo import User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


def get_billing(request: Request) -> BillingService:
    billing = getattr(request.app.state, "billing", None)
    if billing is None:
        raise HTTPException(503, "Database is not available. Check that Docker (Postgres) is running.")
    return billing


async def _user(billing: BillingService, user_id: str) -> User:
    user = await billing.users.get(user_id)
    if user is None:
        raise HTTPException(401, "Please sign in again.")
    return user


async def _call(coro) -> str:
    try:
        return await coro
    except BillingError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except Exception as exc:
        log.exception("payment provider call failed")
        raise HTTPException(502, "The payment service didn't respond. Try again in a minute.") from exc


class CheckoutRequest(BaseModel):
    plan: Literal["pro", "premium"]


@router.post("/checkout")
async def checkout(body: CheckoutRequest, user_id: str = Depends(current_user_id),
                   billing: BillingService = Depends(get_billing)) -> dict:
    """Returns the Stripe Checkout page to redirect to (or the portal, if already subscribed)."""
    user = await _user(billing, user_id)
    return {"url": await _call(billing.checkout(user, body.plan))}


@router.post("/portal")
async def portal(user_id: str = Depends(current_user_id), billing: BillingService = Depends(get_billing)) -> dict:
    user = await _user(billing, user_id)
    return {"url": await _call(billing.portal(user))}


LOCAL_TEST_SECRET_PREFIX = "whsec_local"


@router.post("/webhook")
async def webhook(request: Request) -> dict:
    """Stripe calls this. No user session: the signature proves the event came from Stripe.
    Any error other than a bad signature returns 5xx, so Stripe retries the event later."""
    settings = get_settings()
    secret = settings.stripe_webhook_secret
    if not secret:
        raise HTTPException(503, "Webhooks are not configured (STRIPE_WEBHOOK_SECRET).")
    if secret.startswith(LOCAL_TEST_SECRET_PREFIX) and settings.app_env != "development":
        # The local test secret is published in docs/TECHNICAL.md: anyone could sign a fake "paid" event.
        log.error("STRIPE_WEBHOOK_SECRET is the local test value; set the real one from the Stripe dashboard")
        raise HTTPException(503, "Webhooks are not configured (STRIPE_WEBHOOK_SECRET).")
    payload = await request.body()
    try:
        event = verify_and_parse(payload, request.headers.get("stripe-signature"), secret)
    except InvalidWebhook as exc:
        log.warning("rejected webhook: %s", exc)
        raise HTTPException(400, "Invalid signature.") from exc
    if event["type"] not in HANDLED_EVENTS:
        return {"received": True, "result": "ignored"}
    billing = get_billing(request)
    result = await billing.handle_event(event)
    log.info("stripe event %s %s: %s", event.get("id"), event["type"], result)
    return {"received": True, "result": result}
