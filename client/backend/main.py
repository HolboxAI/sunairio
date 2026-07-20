"""Thin proxy backend in front of the remote Sunairio NL2SQL API.

Streamlit talks only to this service; this service talks to UPSTREAM_API_URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, FastAPI, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

UPSTREAM = os.getenv("UPSTREAM_API_URL", "http://3.148.208.253:8000").rstrip("/")
TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT_SEC", "180"))

app = FastAPI(title="Sunairio NL2SQL Client Backend", version="0.1.0")
router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    email: str
    password: str


class AskRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class RunSqlRequest(BaseModel):
    sql: str


def _auth_headers(authorization: Optional[str]) -> dict:
    return {"Authorization": authorization} if authorization else {}


def _call(method: str, path: str, *, headers: dict, json: Any = None) -> Any:
    url = f"{UPSTREAM}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.request(method, url, headers=headers, json=json)
    except httpx.HTTPError as e:
        logger.warning("upstream %s %s failed: %s", method, path, e)
        raise HTTPException(status_code=502, detail=f"Upstream unreachable: {e}") from e

    try:
        payload = resp.json()
    except ValueError:
        payload = {"detail": resp.text}

    if resp.status_code >= 400:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise HTTPException(status_code=resp.status_code, detail=detail or "Upstream error")
    return payload


@router.get("/health")
def health():
    """Local liveness + upstream reachability."""
    try:
        upstream = _call("GET", "/api/health", headers={})
        upstream_ok = True
    except HTTPException as e:
        upstream, upstream_ok = {"detail": e.detail}, False
    return {"status": "ok", "upstream": UPSTREAM, "upstream_ok": upstream_ok, "upstream_body": upstream}


@router.post("/login")
def login(req: LoginRequest):
    return _call("POST", "/api/login", headers={}, json=req.model_dump())


@router.get("/me")
def me(authorization: Optional[str] = Header(default=None)):
    return _call("GET", "/api/me", headers=_auth_headers(authorization))


@router.post("/ask")
def ask(req: AskRequest, authorization: Optional[str] = Header(default=None)):
    """Run one NL question against the upstream /api/query endpoint."""
    body = {"question": req.question}
    if req.session_id:
        body["session_id"] = req.session_id
    return _call("POST", "/api/query", headers=_auth_headers(authorization), json=body)


@router.post("/run-sql")
def run_sql(req: RunSqlRequest, authorization: Optional[str] = Header(default=None)):
    """Execute SQL directly via the upstream /api/sql endpoint — no LLM involved."""
    sql = (req.sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    return _call("POST", "/api/sql", headers=_auth_headers(authorization), json={"sql": sql})


@router.post("/clear")
def clear(session_id: str = Body(..., embed=True), authorization: Optional[str] = Header(default=None)):
    return _call("POST", "/api/query/clear", headers=_auth_headers(authorization), json={"session_id": session_id})


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("CLIENT_BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("CLIENT_BACKEND_PORT", "8601")),
    )
