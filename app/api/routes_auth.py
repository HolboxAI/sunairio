"""Auth endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import auth
from app.api.schemas import LoginRequest, LoginResponse, RegisterRequest, RegisterResponse
from app.deps import get_current_user
from data import app_db
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
            "status": user.get("status") or "active",
        },
    )


@router.post("/register", response_model=RegisterResponse)
def register(req: RegisterRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password are required")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if app_db.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = app_db.create_user(
        email,
        auth.hash_password(req.password),
        role="user",
        metadata_username=(req.metadata_username or email).strip() or email,
        status="pending_limit",
    )
    user = app_db.get_user_by_id(user_id)
    assert user is not None
    return RegisterResponse(
        message="Account created. An admin must set your monthly token limit before you can query.",
        user={
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "metadata_username": user.get("metadata_username"),
            "status": user.get("status"),
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
            "status": user.get("status") or "active",
        },
        "allowed_entities": ctx.allowed_entities,
    }
