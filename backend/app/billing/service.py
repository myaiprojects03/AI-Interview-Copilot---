"""Subscriptions: checkout, self-service portal, and keeping users.plan in sync with Stripe.

Stripe is the source of truth for who pays. The app never sets a paid plan from the browser:
the plan only changes when a signed webhook event says so. Events can arrive late, twice or out
of order, so every update is idempotent and an older event never overwrites a newer one.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.billing.gateway import BillingGateway
from app.billing.plans import DEFAULT_PLAN, PLANS, Plan
from app.config import Settings
from app.users.repo import User, UserRepo

log = logging.getLogger(__name__)

# past_due: a renewal failed and Stripe is retrying the card. Access continues until Stripe gives
# up and cancels (its retry schedule is set in the Stripe dashboard), then the plan drops to Free.
PAID_STATUSES = frozenset({"active", "trialing", "past_due"})

SUBSCRIPTION_EVENTS = frozenset({
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})
HANDLED_EVENTS = SUBSCRIPTION_EVENTS | {"checkout.session.completed"}


class BillingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _id(value: Any) -> str | None:
    """Stripe fields like `customer` are an id, or the object itself when expanded."""
    if isinstance(value, dict):
        return value.get("id")
    return value or None


class BillingService:
    def __init__(self, users: UserRepo, gateway: BillingGateway | None, settings: Settings):
        self.users = users
        self.gateway = gateway
        self.settings = settings

    # ---------- plans and prices ----------
    def price_id(self, plan: Plan) -> str:
        return getattr(self.settings, plan.price_setting, "") if plan.price_setting else ""

    def plan_for_price(self, price_id: str) -> Plan | None:
        return next((p for p in PLANS.values() if price_id and self.price_id(p) == price_id), None)

    @property
    def enabled(self) -> bool:
        return self.gateway is not None

    def purchasable(self, plan: Plan) -> bool:
        return self.enabled and bool(self.price_id(plan))

    def status_for(self, user: User | None) -> dict:
        return {
            "payments_enabled": self.enabled,
            "subscription_status": user.subscription_status if user else None,
            # Only after a subscription existed: opening checkout alone creates a customer, but there
            # would be nothing to manage. Former subscribers keep access to their invoices.
            "can_manage": bool(self.enabled and user and user.stripe_customer_id and user.subscription_id),
        }

    # ---------- user actions ----------
    def _gateway(self) -> BillingGateway:
        if self.gateway is None:
            raise BillingError(503, "Payments aren't set up yet.")
        return self.gateway

    def _url(self, path: str) -> str:
        return self.settings.frontend_origin.rstrip("/") + path

    async def _customer(self, user: User) -> str:
        if user.stripe_customer_id:
            return user.stripe_customer_id
        created = await self._gateway().create_customer(user_id=user.id, email=user.email, name=user.name)
        return await self.users.set_customer(user.id, created)

    async def checkout(self, user: User, plan_id: str) -> str:
        plan = PLANS.get(plan_id)
        if plan is None or not plan.price_setting:
            raise BillingError(400, "Choose Pro or Premium.")
        gateway = self._gateway()
        price = self.price_id(plan)
        if not price:
            raise BillingError(503, f"The {plan.name} plan can't be bought yet.")
        if user.subscription_status in PAID_STATUSES and user.stripe_customer_id:
            # Already subscribed: switching plans happens in the portal, so nobody pays twice.
            return await gateway.create_portal(customer_id=user.stripe_customer_id,
                                               return_url=self._url("/dashboard"))
        customer = await self._customer(user)
        return await gateway.create_checkout(
            customer_id=customer, price_id=price, user_id=user.id,
            success_url=self._url("/dashboard?checkout=success"),
            cancel_url=self._url("/dashboard?checkout=cancelled"),
        )

    async def portal(self, user: User) -> str:
        gateway = self._gateway()
        if not user.stripe_customer_id:
            raise BillingError(409, "You don't have a subscription yet.")
        return await gateway.create_portal(customer_id=user.stripe_customer_id, return_url=self._url("/dashboard"))

    async def cancel_for_account_deletion(self, user: User) -> None:
        """Deleting the Stripe customer cancels every subscription at once, so nobody is billed
        for an account that no longer exists. Raises if Stripe can't be reached."""
        if not user.stripe_customer_id:
            return
        if self.gateway is None:
            log.warning("user %s has Stripe customer %s but payments are not configured; not cancelled",
                        user.id, user.stripe_customer_id)
            return
        await self.gateway.delete_customer(user.stripe_customer_id)

    # ---------- webhooks ----------
    async def handle_event(self, event: dict[str, Any]) -> str:
        """Apply one verified Stripe event. Returns a short result, for the log and for tests."""
        kind = event.get("type", "")
        obj = event.get("data", {}).get("object", {})
        if kind == "checkout.session.completed":
            return await self._checkout_completed(obj)
        if kind in SUBSCRIPTION_EVENTS:
            at = datetime.fromtimestamp(int(event.get("created", 0)), UTC)
            return await self._subscription_changed(obj, at)
        return "ignored"

    async def _checkout_completed(self, session: dict) -> str:
        user_id, customer = session.get("client_reference_id"), _id(session.get("customer"))
        user = await self.users.get(user_id) if user_id else None
        if user is None or not customer:
            return "unknown user"
        await self.users.set_customer(user.id, customer)
        return "linked"  # the plan itself is set by the subscription events

    async def _user_for(self, sub: dict) -> User | None:
        customer = _id(sub.get("customer"))
        user = await self.users.get_by_customer(customer) if customer else None
        if user is None:
            user_id = (sub.get("metadata") or {}).get("user_id")
            user = await self.users.get(user_id) if user_id else None
            if user is not None and customer:
                if user.stripe_customer_id not in (None, customer):
                    log.warning("event customer %s does not match user %s; ignored", customer, user.id)
                    return None
                await self.users.set_customer(user.id, customer)
        return user

    async def _current(self, sub: dict) -> dict:
        """Stripe can send several events in the same second (created as `incomplete`, then
        `active` once paid) and does not guarantee their order, so the event body may already be
        out of date. Re-reading the subscription means every event applies the latest state."""
        if self.gateway is None or not sub.get("id"):
            return sub  # payments not configured (local simulation): the event body is all there is
        return await self.gateway.get_subscription(sub["id"]) or sub

    async def _subscription_changed(self, sub: dict, at: datetime) -> str:
        sub = await self._current(sub)
        user = await self._user_for(sub)
        if user is None:
            log.warning("subscription %s: no matching user", sub.get("id"))
            return "unknown user"
        sub_id, status = sub.get("id"), sub.get("status")
        if status not in PAID_STATUSES:
            # Only the subscription the user is on can take them back to Free; an old,
            # replaced subscription ending must not downgrade a current one.
            if user.subscription_id and user.subscription_id != sub_id:
                return "ignored: not the current subscription"
            plan_id = DEFAULT_PLAN
        else:
            prices = [_id(item.get("price")) or "" for item in (sub.get("items") or {}).get("data", [])]
            plan = next((p for p in map(self.plan_for_price, prices) if p), None)
            if plan is None:
                log.error("subscription %s has price %s, which matches no plan; set STRIPE_PRICE_* to fix",
                          sub_id, prices)
                return "unknown price"
            plan_id = plan.id
        applied = await self.users.apply_subscription(
            user.id, plan=plan_id, subscription_id=sub_id, status=status, event_at=at)
        if not applied:
            return "ignored: older than the last applied event"
        log.info("user %s: plan %s (subscription %s, %s)", user.id, plan_id, sub_id, status)
        return f"plan {plan_id}"
