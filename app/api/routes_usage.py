"""User token usage endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import UsageBreakdownItem, UsageResponse, UsageSummary
from app.deps import get_current_user
from data import app_db

router = APIRouter(prefix="/api", tags=["usage"])


@router.get("/usage", response_model=UsageResponse)
def get_usage(
    user: dict = Depends(get_current_user),
    granularity: str = Query("summary", pattern="^(summary|question|day|week|month)$"),
):
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    status = user.get("status") or "active"
    summary = None
    if status == "active" and user.get("role") != "admin":
        raw = app_db.build_usage_summary(user_id)
        if raw:
            summary = UsageSummary(**raw)

    breakdown: list[UsageBreakdownItem] = []
    if granularity != "summary":
        rows = app_db.get_usage_breakdown(user_id, granularity)  # type: ignore[arg-type]
        breakdown = [UsageBreakdownItem(**row) for row in rows]

    return UsageResponse(summary=summary, breakdown=breakdown, status=status)
