"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from data import app_db, pools

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready():
    try:
        app_db.init_db()
        pings = pools.ping_all()
        all_ok = all(pings.values())
        return {"status": "ready" if all_ok else "degraded", "backends": pings}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
