"""Authentication: JWT + bcrypt + ACL loading."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import jwt

from config.settings import settings
from data import app_db, metadata_db
from security.acl import UserACL

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, email: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.auth.jwt_expire_hours)
    payload = {"sub": str(user_id), "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, settings.auth.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.auth.jwt_secret, algorithms=["HS256"])


def authenticate_user(email: str, password: str) -> Optional[dict]:
    user = app_db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def seed_default_admin() -> None:
    if app_db.get_user_by_email(settings.auth.default_admin_email):
        return
    app_db.create_user(
        settings.auth.default_admin_email,
        hash_password(settings.auth.default_admin_password),
        role="admin",
        metadata_username=settings.auth.default_admin_email,
    )
    logger.info("Seeded default admin: %s", settings.auth.default_admin_email)


def get_acl_for_user(user: dict) -> UserACL:
    meta_username = user.get("metadata_username") or user["email"]
    if user.get("role") == "admin":
        return UserACL(username=meta_username, is_admin=True)
    try:
        return metadata_db.load_user_acl(meta_username)
    except Exception as e:
        logger.warning("Could not load ACL for %s: %s", meta_username, e)
        return UserACL(username=meta_username)
