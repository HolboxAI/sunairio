"""FastAPI dependencies."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from app import auth
from config.settings import settings
from data import app_db

_bearer = HTTPBearer(auto_error=False)


def new_request_id() -> str:
    return uuid.uuid4().hex


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    if not settings.auth.auth_required:
        return {"id": 0, "email": "anonymous", "role": "admin", "metadata_username": None}
    token = credentials.credentials if credentials else request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = auth.decode_token(token)
        user = app_db.get_user_by_email(payload["email"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
