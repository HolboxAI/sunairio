"""Auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import auth
from app.api.schemas import LoginRequest, LoginResponse
from app.deps import get_current_user
from core.models import ConversationState
from core.session_context import build_session_context

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    user = auth.authenticate_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = auth.create_access_token(user["id"], user["email"], user["role"])
    return LoginResponse(
        access_token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "metadata_username": user.get("metadata_username"),
        },
    )


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    acl = auth.get_acl_for_user(user)
    state = ConversationState()
    ctx = build_session_context(user, acl, state)
    return {
        "user": {
            "id": user.get("id"),
            "email": user.get("email"),
            "role": user.get("role"),
            "metadata_username": user.get("metadata_username"),
        },
        "allowed_entities": ctx.allowed_entities,
    }
