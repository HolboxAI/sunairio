"""Application settings loaded from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


def _resolve_path(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p).resolve()


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    name: str
    user: str
    password: str
    sslmode: str


@dataclass(frozen=True)
class LakeSettings:
    host: str
    port: int
    user: str
    password: str


@dataclass(frozen=True)
class BedrockSettings:
    region: str
    model_id: str
    max_tokens: int
    read_timeout_sec: int
    connect_timeout_sec: int


@dataclass(frozen=True)
class SafetySettings:
    query_timeout_sec: int
    max_query_rows: int


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    jwt_expire_hours: int
    auth_required: bool
    default_admin_email: str
    default_admin_password: str


@dataclass(frozen=True)
class Settings:
    app_db_path: Path
    metadata_db: PostgresSettings
    forecast_db: PostgresSettings
    lake: LakeSettings
    bedrock: BedrockSettings
    auth: AuthSettings
    safety: SafetySettings
    prompt_path: Path
    llm_audit_log_dir: Path
    analytics_consult_log_dir: Path
    port: int


def _load() -> Settings:
    base = Path(__file__).resolve().parent.parent
    return Settings(
        app_db_path=_resolve_path(base, os.getenv("APP_DB_PATH", "data/sunairio_nl2sql.db")),
        metadata_db=PostgresSettings(
            host=os.getenv("METADATA_DB_HOST", ""),
            port=int(os.getenv("METADATA_DB_PORT", "5432")),
            name=os.getenv("METADATA_DB_NAME", "sunairio"),
            user=os.getenv("METADATA_DB_USER", ""),
            password=os.getenv("METADATA_DB_PASSWORD", ""),
            sslmode=os.getenv("METADATA_DB_SSLMODE", "require"),
        ),
        forecast_db=PostgresSettings(
            host=os.getenv("FORECAST_DB_HOST", ""),
            port=int(os.getenv("FORECAST_DB_PORT", "5432")),
            name=os.getenv("FORECAST_DB_NAME", "forecast"),
            user=os.getenv("FORECAST_DB_USER", ""),
            password=os.getenv("FORECAST_DB_PASSWORD", ""),
            sslmode=os.getenv("FORECAST_DB_SSLMODE", "require"),
        ),
        lake=LakeSettings(
            host=os.getenv("LAKE_HOST", ""),
            port=int(os.getenv("LAKE_PORT", "32011")),
            user=os.getenv("LAKE_USER", ""),
            password=os.getenv("LAKE_PASSWORD", ""),
        ),
        bedrock=BedrockSettings(
            region=os.getenv("AWS_REGION", "us-east-2"),
            model_id=os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
            max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "4096")),
            read_timeout_sec=int(os.getenv("BEDROCK_READ_TIMEOUT_SEC", "120")),
            connect_timeout_sec=int(os.getenv("BEDROCK_CONNECT_TIMEOUT_SEC", "10")),
        ),
        auth=AuthSettings(
            jwt_secret=os.getenv("JWT_SECRET", "change-me-in-production"),
            jwt_expire_hours=int(os.getenv("JWT_EXPIRE_HOURS", "24")),
            auth_required=_bool("AUTH_REQUIRED", False),
            default_admin_email=os.getenv("DEFAULT_ADMIN_EMAIL", "admin@sunairio.local"),
            default_admin_password=os.getenv("DEFAULT_ADMIN_PASSWORD", "changeme123"),
        ),
        safety=SafetySettings(
            query_timeout_sec=int(os.getenv("QUERY_TIMEOUT_SEC", "30")),
            max_query_rows=int(os.getenv("MAX_QUERY_ROWS", "5000")),
        ),
        prompt_path=_resolve_path(base, os.getenv("PROMPT_PATH", "prompts/sunairio-sql-prompt.md")),
        llm_audit_log_dir=_resolve_path(base, os.getenv("LLM_AUDIT_LOG_DIR", "logs/llm-audit")),
        analytics_consult_log_dir=_resolve_path(
            base, os.getenv("ANALYTICS_CONSULT_LOG_DIR", "logs/analytics-consult")
        ),
        port=int(os.getenv("PORT", "8003")),
    )


settings = _load()
