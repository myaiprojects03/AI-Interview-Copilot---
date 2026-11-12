"""Plans, usage and interview history endpoints."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.auth import current_user_id
from app.billing.plans import PLANS, limit_seconds, plan_for
from app.config import is_admin
from app.history.repo import HistoryRepo

router = APIRouter(prefix="/api", tags=["history"])


def get_history(request: Request) -> HistoryRepo:
    history = getattr(request.app.state, "history", None)
    if history is None:
        raise HTTPException(503, "Database is not available. Check that Docker (Postgres) is running.")
    return history


def _next_month_start() -> datetime:
    now = datetime.now(UTC)
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


@router.get("/plans")
async def plans(request: Request) -> list[dict]:
    """Public: used by the pricing section of the landing page and the dashboard."""
    billing = getattr(request.app.state, "billing", None)
    return [{**p.public(), "purchasable": bool(billing and billing.purchasable(p))} for p in PLANS.values()]


@router.get("/usage")
async def usage(request: Request, user_id: str = Depends(current_user_id),
                history: HistoryRepo = Depends(get_history)) -> dict:
    users = getattr(request.app.state, "users", None)
    user = await users.get(user_id) if users else None
    plan = plan_for(user.plan if user else None)
    limit = limit_seconds(plan)
    used = await history.used_seconds_this_month(user_id)
    billing = getattr(request.app.state, "billing", None)
    return {
        "plan": plan.public(),
        "billing": billing.status_for(user) if billing else
        {"payments_enabled": False, "subscription_status": None, "can_manage": False},
        "used_seconds": used,
        "limit_seconds": limit,
        "remaining_seconds": None if limit is None else max(0, limit - used),
        "resets_at": _next_month_start().isoformat(),
    }


@router.get("/sessions")
async def list_sessions(user_id: str = Depends(current_user_id),
                        history: HistoryRepo = Depends(get_history)) -> list[dict]:
    return [s.public() for s in await history.list_sessions(user_id)]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user_id: str = Depends(current_user_id),
                      history: HistoryRepo = Depends(get_history)) -> dict:
    detail = await history.get_session(user_id, session_id)
    if detail is None:
        raise HTTPException(404, "Interview not found.")
    return detail.public()


@router.delete("/sessions/{session_id}", status_code=204, response_class=Response)
async def delete_session(session_id: str, user_id: str = Depends(current_user_id),
                         history: HistoryRepo = Depends(get_history)) -> Response:
    if not await history.delete_session(user_id, session_id):
        raise HTTPException(404, "Interview not found.")
    return Response(status_code=204)


async def admin_user_id(request: Request, user_id: str = Depends(current_user_id)) -> str:
    users = getattr(request.app.state, "users", None)
    user = await users.get(user_id) if users else None
    if user is None or not is_admin(user.email):
        raise HTTPException(403, "Only administrators can see this.")
    return user_id


@router.get("/admin/latency")
async def latency(days: int = Query(7, ge=1, le=90), _: str = Depends(admin_user_id),
                  history: HistoryRepo = Depends(get_history)) -> dict:
    """Answer latency across all users (no transcript content), measured on the server from the
    moment the interviewer's final words arrive. Admins only (ADMIN_EMAILS)."""
    since = datetime.now(UTC) - timedelta(days=days)
    stats = await history.latency_stats(since)
    return {"days": days, "since": since.isoformat(), "answers": stats.answers, "metrics": stats.metrics,
            "first_words_under_2s": stats.under_2s}
