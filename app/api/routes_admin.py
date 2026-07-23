"""Admin endpoints — user management, limits, history, usage."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import (
    AdminUserItem,
    AdminUserListResponse,
    HistorySessionItem,
    HistorySessionListResponse,
    HistoryThreadResponse,
    IncreaseTokenLimitRequest,
    SetTokenLimitRequest,
    UsageBreakdownItem,
    UsageResponse,
    UsageSummary,
)
from app.deps import require_admin
from data import app_db

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _usage_summary_or_none(user_id: int) -> UsageSummary | None:
    raw = app_db.build_usage_summary(user_id)
    return UsageSummary(**raw) if raw else None


@router.get("/users", response_model=AdminUserListResponse)
def list_users(admin: dict = Depends(require_admin)):
    items = []
    for user in app_db.list_users():
        usage = None
        if user.get("status") == "active" and user.get("role") != "admin":
            usage = _usage_summary_or_none(user["id"])
        items.append(
            AdminUserItem(
                id=user["id"],
                email=user["email"],
                role=user["role"],
                metadata_username=user.get("metadata_username"),
                status=user.get("status") or "active",
                created_at=user["created_at"],
                usage=usage,
            )
        )
    return AdminUserListResponse(items=items)


@router.patch("/users/{user_id}/limit")
def set_user_limit(
    user_id: int,
    req: SetTokenLimitRequest,
    admin: dict = Depends(require_admin),
):
    if req.base_monthly_limit <= 0:
        raise HTTPException(status_code=400, detail="base_monthly_limit must be positive")
    user = app_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("role") == "admin":
        raise HTTPException(status_code=400, detail="Cannot set limits on admin users")

    existing = app_db.get_user_token_limit(user_id)
    if existing:
        app_db.set_user_token_limit(user_id, req.base_monthly_limit, existing["cycle_anchor_date"])
    else:
        anchor = date.today().isoformat()
        app_db.set_user_token_limit(user_id, req.base_monthly_limit, anchor)

    summary = _usage_summary_or_none(user_id)
    return {"ok": True, "usage": summary}


@router.post("/users/{user_id}/limit/increase")
def increase_user_limit(
    user_id: int,
    req: IncreaseTokenLimitRequest,
    admin: dict = Depends(require_admin),
):
    if req.bonus_tokens <= 0:
        raise HTTPException(status_code=400, detail="bonus_tokens must be positive")
    user = app_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    updated = app_db.increase_user_token_limit(user_id, req.bonus_tokens)
    if not updated:
        raise HTTPException(status_code=400, detail="User has no token limit configured yet")
    summary = _usage_summary_or_none(user_id)
    return {"ok": True, "usage": summary}


@router.get("/users/{user_id}/usage", response_model=UsageResponse)
def admin_user_usage(
    user_id: int,
    granularity: str = Query("summary", pattern="^(summary|question|day|week|month)$"),
    admin: dict = Depends(require_admin),
):
    user = app_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    summary = _usage_summary_or_none(user_id)
    breakdown: list[UsageBreakdownItem] = []
    if granularity != "summary":
        rows = app_db.get_usage_breakdown(user_id, granularity)  # type: ignore[arg-type]
        breakdown = [UsageBreakdownItem(**row) for row in rows]

    return UsageResponse(
        summary=summary,
        breakdown=breakdown,
        status=user.get("status") or "active",
    )


@router.get("/users/{user_id}/history", response_model=HistorySessionListResponse)
def admin_user_history(
    user_id: int,
    limit: int = Query(100, ge=1, le=500),
    admin: dict = Depends(require_admin),
):
    user = app_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    items = app_db.list_conversation_sessions(user_id, limit=limit)
    return HistorySessionListResponse(items=[HistorySessionItem(**item) for item in items])


@router.get("/users/{user_id}/history/thread", response_model=HistoryThreadResponse)
def admin_user_history_thread(
    user_id: int,
    session_id: str = Query(...),
    admin: dict = Depends(require_admin),
):
    user = app_db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    turns = app_db.get_session_thread(user_id, session_id)
    title = app_db.get_session_display_title(user_id, session_id)
    return HistoryThreadResponse(session_id=session_id, title=title, turns=turns)
