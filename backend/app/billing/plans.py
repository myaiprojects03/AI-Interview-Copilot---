"""Subscription plans: the one place to change plan names, prices and limits.

Prices are example values (the project brief leaves pricing to be confirmed). Features list only what
the product actually does: plans differ by monthly minutes, nothing else. `price_setting` names the
setting holding the matching Stripe Price ID; free plans have none.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_label: str
    monthly_minutes: int | None     # None = unlimited
    features: tuple[str, ...]
    price_setting: str | None = None   # Settings attribute with the Stripe Price ID

    def public(self) -> dict:
        d = asdict(self)
        d.pop("price_setting")
        d["features"] = list(self.features)
        return d


PLANS: dict[str, Plan] = {p.id: p for p in (
    Plan("free", "Free", "$0", 30,
         ("30 interview minutes a month", "Answers from your CV and job description", "Interview history")),
    Plan("pro", "Pro", "$19 / month", 300,
         ("300 interview minutes a month", "Everything in Free"),
         price_setting="stripe_price_pro"),
    Plan("premium", "Premium", "$49 / month", None,
         ("Unlimited interview minutes", "Everything in Free"),
         price_setting="stripe_price_premium"),
)}
DEFAULT_PLAN = "free"


def plan_for(plan_id: str | None) -> Plan:
    return PLANS.get(plan_id or DEFAULT_PLAN, PLANS[DEFAULT_PLAN])


def limit_seconds(plan: Plan) -> int | None:
    return None if plan.monthly_minutes is None else plan.monthly_minutes * 60
