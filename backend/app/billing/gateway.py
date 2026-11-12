"""Payment provider interface and the Stripe implementation.

The billing logic (service.py) only talks to `BillingGateway`, so tests use a fake and another
payment provider could be added without touching it.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import stripe

from app.config import Settings

log = logging.getLogger(__name__)

WEBHOOK_TOLERANCE_S = 300  # reject signed events older than 5 minutes (replay protection)


class InvalidWebhook(Exception):
    """The request is not a genuine, recent event from the payment provider."""


class BillingGateway(ABC):
    @abstractmethod
    async def create_customer(self, *, user_id: str, email: str, name: str) -> str: ...

    @abstractmethod
    async def create_checkout(self, *, customer_id: str, price_id: str, user_id: str,
                              success_url: str, cancel_url: str) -> str:
        """Start a subscription checkout; returns the page to send the user to."""

    @abstractmethod
    async def create_portal(self, *, customer_id: str, return_url: str) -> str:
        """Self-service page for changing plan, card or cancelling; returns its URL."""

    @abstractmethod
    async def delete_customer(self, customer_id: str) -> None:
        """Delete the customer, which immediately cancels their subscriptions. Already gone = success."""

    @abstractmethod
    async def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        """The subscription as it is now (None if Stripe has no such subscription)."""


class StripeGateway(BillingGateway):
    def __init__(self, settings: Settings):
        base = {"api": settings.stripe_api_base} if settings.stripe_api_base else None
        self.client = stripe.StripeClient(
            settings.stripe_secret_key,
            base_addresses=base,  # type: ignore[arg-type]
            http_client=stripe.HTTPXClient(timeout=20),
            max_network_retries=2,  # the SDK retries safely using idempotency keys
        )

    async def create_customer(self, *, user_id: str, email: str, name: str) -> str:
        customer = await self.client.v1.customers.create_async(
            {"email": email, "name": name or email, "metadata": {"user_id": user_id}},
            # A double click within 24 h returns the same customer instead of creating two.
            {"idempotency_key": f"customer-{user_id}"},
        )
        return customer.id

    async def create_checkout(self, *, customer_id: str, price_id: str, user_id: str,
                              success_url: str, cancel_url: str) -> str:
        session = await self.client.v1.checkout.sessions.create_async({
            "mode": "subscription",
            "customer": customer_id,
            "client_reference_id": user_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "subscription_data": {"metadata": {"user_id": user_id}},
            "allow_promotion_codes": True,
            "success_url": success_url,
            "cancel_url": cancel_url,
        })
        if not session.url:
            raise RuntimeError("Stripe returned a checkout session without a URL")
        return session.url

    async def create_portal(self, *, customer_id: str, return_url: str) -> str:
        portal = await self.client.v1.billing_portal.sessions.create_async(
            {"customer": customer_id, "return_url": return_url})
        return portal.url

    async def delete_customer(self, customer_id: str) -> None:
        try:
            await self.client.v1.customers.delete_async(customer_id)
        except stripe.InvalidRequestError as exc:
            if exc.code != "resource_missing":
                raise
            log.info("stripe customer %s was already deleted", customer_id)

    async def get_subscription(self, subscription_id: str) -> dict[str, Any] | None:
        try:
            sub = await self.client.v1.subscriptions.retrieve_async(subscription_id)
        except stripe.InvalidRequestError as exc:
            if exc.code == "resource_missing":
                return None
            raise
        return sub.to_dict()


def verify_and_parse(payload: bytes, signature: str | None, secret: str) -> dict[str, Any]:
    """Check the Stripe-Signature header (HMAC of the raw body with the endpoint secret, plus a
    timestamp to stop replays) and return the event. Raises InvalidWebhook."""
    try:
        stripe.WebhookSignature.verify_header(payload, signature, secret, tolerance=WEBHOOK_TOLERANCE_S)
        event = json.loads(payload)
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise InvalidWebhook(str(exc)) from exc
    if not isinstance(event, dict) or "type" not in event or "data" not in event:
        raise InvalidWebhook("not an event")
    return event


def build_gateway(settings: Settings) -> BillingGateway | None:
    """None when payments are not configured: the app runs, paid plans just can't be bought yet."""
    if not settings.stripe_secret_key:
        return None
    return StripeGateway(settings)


def sign_webhook_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header the way Stripe does. Used by the tests and by the local
    tool that simulates subscription events (scripts/stripe_event.py); never needed in production."""
    import hashlib
    import hmac
    import time

    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"
