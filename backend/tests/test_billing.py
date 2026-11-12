"""Stripe billing: checkout, portal, webhooks keeping the plan in sync, cancellation on deletion."""
import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.api import billing_routes
from app.billing.gateway import BillingGateway, sign_webhook_payload
from app.billing.service import BillingService
from app.config import Settings
from app.history.repo import InMemoryHistoryRepo
from app.main import app
from app.users.repo import InMemoryUserRepo, User
from tests.conftest import TEST_USER

SECRET = "whsec_test_secret"
PRICES = {"stripe_price_pro": "price_pro", "stripe_price_premium": "price_premium"}


class FakeGateway(BillingGateway):
    def __init__(self):
        self.calls: list[tuple] = []
        self.fail_delete = False
        self.customers = 0
        self.subscriptions: dict[str, dict] = {}  # what Stripe would return now; empty = use the event

    async def create_customer(self, *, user_id, email, name):
        self.customers += 1
        self.calls.append(("customer", user_id, email))
        return f"cus_{self.customers}"

    async def create_checkout(self, *, customer_id, price_id, user_id, success_url, cancel_url):
        self.calls.append(("checkout", customer_id, price_id, success_url, cancel_url))
        return f"https://checkout.example/{price_id}"

    async def create_portal(self, *, customer_id, return_url):
        self.calls.append(("portal", customer_id, return_url))
        return f"https://portal.example/{customer_id}"

    async def delete_customer(self, customer_id):
        if self.fail_delete:
            raise RuntimeError("stripe down")
        self.calls.append(("delete", customer_id))

    async def get_subscription(self, subscription_id):
        return self.subscriptions.get(subscription_id)


def _settings(**kw):
    values = {"stripe_secret_key": "sk_test_x", "stripe_webhook_secret": SECRET,
              "frontend_origin": "https://app.example", **PRICES, **kw}
    return Settings(_env_file=None, **values)


@pytest.fixture
def env(memory_repo, monkeypatch):
    users, history, gateway = InMemoryUserRepo(), InMemoryHistoryRepo(), FakeGateway()
    users.users[TEST_USER] = User(TEST_USER, "test@example.com", "Test")
    settings = _settings()
    monkeypatch.setattr(billing_routes, "get_settings", lambda: settings)
    prev = {k: getattr(app.state, k, None) for k in ("users", "history", "billing")}
    app.state.users, app.state.history = users, history
    app.state.billing = BillingService(users, gateway, settings)
    yield users, gateway, TestClient(app)
    for k, v in prev.items():
        setattr(app.state, k, v)


def _sub_event(kind="customer.subscription.updated", *, sub="sub_1", customer="cus_1", status="active",
               price="price_pro", created=None, metadata=None):
    return {
        "id": f"evt_{time.time_ns()}", "type": kind, "created": created or int(time.time()),
        "data": {"object": {"id": sub, "object": "subscription", "customer": customer, "status": status,
                            "metadata": metadata or {}, "items": {"data": [{"price": {"id": price}}]}}},
    }


def _send(client, event, secret=SECRET, timestamp=None):
    body = json.dumps(event).encode()
    return client.post("/api/billing/webhook", content=body,
                       headers={"stripe-signature": sign_webhook_payload(body, secret, timestamp)})


def _subscribe(users, client, **kw):
    users.users[TEST_USER].stripe_customer_id = "cus_1"
    return _send(client, _sub_event(**kw))


# ---------- plans and checkout ----------
def test_plans_show_what_can_be_bought(env):
    _, _, client = env
    plans = {p["id"]: p for p in client.get("/api/plans").json()}
    assert not plans["free"]["purchasable"] and plans["pro"]["purchasable"] and plans["premium"]["purchasable"]
    app.state.billing.gateway = None  # no STRIPE_SECRET_KEY
    assert not any(p["purchasable"] for p in client.get("/api/plans").json())
    assert client.get("/api/usage").json()["billing"] == {
        "payments_enabled": False, "subscription_status": None, "can_manage": False}


def test_checkout_creates_customer_once_and_returns_stripe_page(env):
    users, gateway, client = env
    r = client.post("/api/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200 and r.json()["url"] == "https://checkout.example/price_pro"
    assert gateway.calls[0] == ("customer", TEST_USER, "test@example.com")
    assert gateway.calls[1] == ("checkout", "cus_1", "price_pro", "https://app.example/dashboard?checkout=success",
                                "https://app.example/dashboard?checkout=cancelled")
    client.post("/api/billing/checkout", json={"plan": "premium"})
    assert gateway.customers == 1 and gateway.calls[-1][2] == "price_premium"
    assert users.users[TEST_USER].plan == "free"  # only a webhook can change the plan


def test_checkout_rejects_free_plan_and_missing_setup(env):
    _, _, client = env
    assert client.post("/api/billing/checkout", json={"plan": "free"}).status_code == 422
    app.state.billing.settings = _settings(stripe_price_premium="")
    r = client.post("/api/billing/checkout", json={"plan": "premium"})
    assert r.status_code == 503 and "Premium" in r.json()["detail"]
    app.state.billing.gateway = None
    r = client.post("/api/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 503 and r.json()["detail"] == "Payments aren't set up yet."


def test_subscribed_user_is_sent_to_portal_not_a_second_checkout(env):
    users, gateway, client = env
    _subscribe(users, client)
    r = client.post("/api/billing/checkout", json={"plan": "premium"})
    assert r.json()["url"] == "https://portal.example/cus_1"
    assert not any(c[0] == "checkout" for c in gateway.calls)


def test_portal_needs_a_customer(env):
    users, _, client = env
    assert client.post("/api/billing/portal").status_code == 409
    users.users[TEST_USER].stripe_customer_id = "cus_9"  # checkout opened, never paid
    assert client.get("/api/usage").json()["billing"]["can_manage"] is False
    users.users[TEST_USER].subscription_id = "sub_9"
    assert client.post("/api/billing/portal").json()["url"] == "https://portal.example/cus_9"
    assert client.get("/api/usage").json()["billing"]["can_manage"] is True


def test_payment_service_errors_are_reported_plainly(env):
    _, gateway, client = env

    async def boom(**kw):
        raise RuntimeError("network")
    gateway.create_customer = boom
    r = client.post("/api/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 502 and "payment service" in r.json()["detail"]


# ---------- webhooks ----------
def test_webhook_rejects_bad_signature_and_replays(env):
    _, _, client = env
    event = _sub_event()
    assert _send(client, event, secret="whsec_wrong").status_code == 400
    assert _send(client, event, timestamp=int(time.time()) - 3600).status_code == 400
    r = client.post("/api/billing/webhook", content=json.dumps(event).encode())
    assert r.status_code == 400
    r = client.post("/api/billing/webhook", content=b"not json",
                    headers={"stripe-signature": sign_webhook_payload(b"not json", SECRET)})
    assert r.status_code == 400


def test_webhook_needs_secret(env, monkeypatch):
    _, _, client = env
    monkeypatch.setattr(billing_routes, "get_settings", lambda: _settings(stripe_webhook_secret=""))
    assert _send(client, _sub_event()).status_code == 503


def test_subscription_lifecycle_sets_plan_and_limits(env):
    users, _, client = env
    u = users.users[TEST_USER]
    t = int(time.time())
    assert _subscribe(users, client, kind="customer.subscription.created", created=t).json()["result"] == "plan pro"
    assert u.plan == "pro" and u.subscription_status == "active" and u.subscription_id == "sub_1"
    usage = client.get("/api/usage").json()
    assert usage["limit_seconds"] == 300 * 60 and usage["billing"]["subscription_status"] == "active"

    _send(client, _sub_event(price="price_premium", created=t + 1))
    assert u.plan == "premium" and client.get("/api/usage").json()["limit_seconds"] is None

    _send(client, _sub_event(price="price_premium", status="past_due", created=t + 2))
    assert u.plan == "premium" and u.subscription_status == "past_due"  # Stripe is still retrying the card

    _send(client, _sub_event(kind="customer.subscription.deleted", status="canceled", created=t + 3))
    assert u.plan == "free" and u.subscription_status == "canceled"


def test_out_of_order_and_duplicate_events(env):
    users, _, client = env
    u = users.users[TEST_USER]
    t = int(time.time())
    newer = _sub_event(kind="customer.subscription.deleted", status="canceled", created=t)
    older = _sub_event(status="active", created=t - 60)
    _subscribe(users, client, created=t - 120)
    assert _send(client, newer).json()["result"] == "plan free"
    r = _send(client, older)  # delivered late
    assert r.status_code == 200 and r.json()["result"].startswith("ignored") and u.plan == "free"
    assert _send(client, newer).json()["result"] == "plan free"  # duplicate delivery is harmless


def test_ending_an_old_subscription_does_not_downgrade_the_current_one(env):
    users, _, client = env
    t = int(time.time())
    _subscribe(users, client, sub="sub_new", created=t)
    r = _send(client, _sub_event(kind="customer.subscription.deleted", sub="sub_old", status="canceled",
                                 created=t + 1))
    assert r.json()["result"] == "ignored: not the current subscription"
    assert users.users[TEST_USER].plan == "pro"


def test_same_second_events_in_wrong_order_still_end_paid(env):
    """Checkout creates the subscription as `incomplete` and activates it the same second. If the
    `created` event is delivered after the `updated` one, the current state from Stripe wins."""
    users, gateway, client = env
    t = int(time.time())
    gateway.subscriptions["sub_1"] = _sub_event(status="active")["data"]["object"]
    _subscribe(users, client, status="active", created=t)
    r = _send(client, _sub_event(kind="customer.subscription.created", status="incomplete", created=t))
    assert r.json()["result"] == "plan pro" and users.users[TEST_USER].plan == "pro"


def test_incomplete_payment_does_not_grant_a_plan(env):
    users, _, client = env
    _subscribe(users, client, kind="customer.subscription.created", status="incomplete")
    assert users.users[TEST_USER].plan == "free"


def test_unknown_price_changes_nothing(env):
    users, _, client = env
    assert _subscribe(users, client, price="price_somebody_else").json()["result"] == "unknown price"
    assert users.users[TEST_USER].plan == "free"


def test_customer_is_found_through_metadata_and_checkout(env):
    users, _, client = env
    u = users.users[TEST_USER]
    r = _send(client, _sub_event(customer="cus_7", metadata={"user_id": TEST_USER}))
    assert r.json()["result"] == "plan pro" and u.stripe_customer_id == "cus_7"
    # An event naming another user's id with a different customer is not applied to them.
    r = _send(client, _sub_event(customer="cus_other", metadata={"user_id": TEST_USER}))
    assert r.json()["result"] == "unknown user" and u.plan == "pro"

    users.users["u2"] = User("u2", "two@example.com")
    done = {"id": "evt_c", "type": "checkout.session.completed", "created": int(time.time()),
            "data": {"object": {"client_reference_id": "u2", "customer": "cus_2"}}}
    assert _send(client, done).json()["result"] == "linked" and users.users["u2"].stripe_customer_id == "cus_2"
    assert users.users["u2"].plan == "free"


def test_unhandled_events_are_acknowledged(env):
    _, _, client = env
    r = _send(client, {"id": "evt_x", "type": "invoice.paid", "created": 1, "data": {"object": {}}})
    assert r.status_code == 200 and r.json()["result"] == "ignored"


# ---------- account deletion ----------
def test_account_deletion_cancels_subscription_first(env):
    users, gateway, client = env
    _subscribe(users, client)
    gateway.fail_delete = True
    r = client.delete("/api/me")
    assert r.status_code == 502 and TEST_USER in users.users  # nothing deleted, nobody billed for a ghost
    gateway.fail_delete = False
    assert client.delete("/api/me").status_code == 204
    assert ("delete", "cus_1") in gateway.calls and TEST_USER not in users.users


# ---------- Postgres ----------
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_postgres_billing_columns():
    from datetime import UTC, datetime, timedelta

    from app.db.migrations import connect
    from app.users.repo import PostgresUserRepo

    async def run():
        pool = await connect(os.environ["TEST_DATABASE_URL"])
        try:
            await pool.execute("DELETE FROM users WHERE email LIKE '%@billtest.example'")
            users = PostgresUserRepo(pool)
            u = await users.create("a@billtest.example", "A", None)
            assert await users.set_customer(u.id, "cus_pg_1") == "cus_pg_1"
            assert await users.set_customer(u.id, "cus_pg_2") == "cus_pg_1"  # first one wins
            assert (await users.get_by_customer("cus_pg_1")).id == u.id
            now = datetime.now(UTC)
            assert await users.apply_subscription(u.id, plan="pro", subscription_id="sub_a", status="active",
                                                  event_at=now)
            assert not await users.apply_subscription(u.id, plan="free", subscription_id="sub_a",
                                                      status="canceled", event_at=now - timedelta(seconds=5))
            got = await users.get(u.id)
            assert (got.plan, got.subscription_status, got.subscription_id) == ("pro", "active", "sub_a")
            await users.delete(u.id)
        finally:
            await pool.close()

    asyncio.run(run())


# ---------- the real Stripe client, against stripe-mock ----------
@pytest.mark.skipif(not os.environ.get("STRIPE_MOCK_URL"), reason="STRIPE_MOCK_URL not set (run stripe-mock)")
def test_stripe_gateway_against_stripe_mock():
    """stripe-mock validates every request against Stripe's API spec, so this catches wrong
    parameter names or shapes without a Stripe account."""
    from app.billing.gateway import StripeGateway

    gw = StripeGateway(Settings(_env_file=None, stripe_secret_key="sk_test_123",
                                stripe_api_base=os.environ["STRIPE_MOCK_URL"]))

    async def run():
        customer = await gw.create_customer(user_id="u1", email="a@example.com", name="A")
        assert customer.startswith("cus_")
        url = await gw.create_checkout(customer_id=customer, price_id="price_123", user_id="u1",
                                       success_url="https://app.example/s", cancel_url="https://app.example/c")
        assert url.startswith("https://")
        assert (await gw.create_portal(customer_id=customer, return_url="https://app.example/d")).startswith("https://")
        sub = await gw.get_subscription("sub_123")
        assert sub and sub["object"] == "subscription" and "status" in sub and sub["items"]["data"][0]["price"]["id"]
        await gw.delete_customer(customer)

    asyncio.run(run())
