"""Simulate Stripe subscription events locally, without a Stripe account.

Builds the same signed webhook Stripe would send and posts it to the running backend, so the whole
path (signature check -> plan update -> minute limits -> dashboard) can be tested and demoed.

    python -m scripts.stripe_event --email you@example.com --plan pro
    python -m scripts.stripe_event --email you@example.com --plan premium
    python -m scripts.stripe_event --email you@example.com --status past_due
    python -m scripts.stripe_event --email you@example.com --cancel

Needs in .env: STRIPE_WEBHOOK_SECRET (any value locally, e.g. whsec_local_test),
STRIPE_PRICE_PRO and STRIPE_PRICE_PREMIUM (e.g. price_local_pro / price_local_premium).
With real Stripe, use `stripe listen --forward-to localhost:8000/api/billing/webhook` instead.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import asyncpg
import httpx

from app.billing.gateway import sign_webhook_payload
from app.config import get_settings


async def _user(dsn: str, email: str) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT id, plan, stripe_customer_id, subscription_id FROM users WHERE email = $1",
            email.strip().lower())
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="the account to change")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", choices=["pro", "premium"], help="start or switch to this plan")
    group.add_argument("--status", choices=["active", "past_due", "unpaid"], help="change the payment status")
    group.add_argument("--cancel", action="store_true", help="end the subscription (back to Free)")
    parser.add_argument("--url", default="http://localhost:8000/api/billing/webhook")
    args = parser.parse_args()

    s = get_settings()
    if s.app_env == "production":
        print("Refusing to run with APP_ENV=production.")
        return 1
    if not s.stripe_webhook_secret:
        print("Set STRIPE_WEBHOOK_SECRET in .env first (any value for local testing), then restart the backend.")
        return 1

    user = asyncio.run(_user(s.database_url, args.email))
    if user is None:
        print(f"No account with email {args.email}. Sign up in the app first.")
        return 1

    uid = str(user["id"])
    customer = user["stripe_customer_id"] or f"cus_local_{uid[:8]}"
    subscription = user["subscription_id"] or f"sub_local_{uid[:8]}"
    current = user["plan"] if user["plan"] != "free" else "pro"
    plan = args.plan or current
    price = s.stripe_price_pro if plan == "pro" else s.stripe_price_premium
    if not price:
        print(f"Set STRIPE_PRICE_{plan.upper()} in .env first (e.g. price_local_{plan}), then restart the backend.")
        return 1

    kind = "customer.subscription.deleted" if args.cancel else "customer.subscription.updated"
    status = "canceled" if args.cancel else (args.status or "active")
    event = {
        "id": f"evt_local_{time.time_ns()}",
        "object": "event",
        "type": kind,
        "created": int(time.time()),
        "livemode": False,
        "data": {"object": {
            "id": subscription, "object": "subscription", "customer": customer, "status": status,
            "metadata": {"user_id": uid},
            "items": {"object": "list", "data": [{"object": "subscription_item", "price": {"id": price}}]},
        }},
    }
    body = json.dumps(event).encode()
    try:
        r = httpx.post(args.url, content=body, timeout=10, headers={
            "content-type": "application/json",
            "stripe-signature": sign_webhook_payload(body, s.stripe_webhook_secret),
        })
    except httpx.HTTPError as exc:
        print(f"Could not reach the backend at {args.url}: {exc}")
        return 1
    print(f"{kind} ({status}) -> HTTP {r.status_code}: {r.text}")
    return 0 if r.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
