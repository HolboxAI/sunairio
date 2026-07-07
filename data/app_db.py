"""App DB — users, conversation state, query log, LLM audit index (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db():
    path = settings.app_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                metadata_username TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversation_state (
                session_id TEXT PRIMARY KEY,
                entity_shortname TEXT,
                location_key TEXT,
                variable TEXT,
                timeframe TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS query_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                request_id TEXT NOT NULL,
                session_id TEXT,
                question TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS llm_audit_index (
                request_id TEXT PRIMARY KEY,
                log_file_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(
    email: str,
    password_hash: str,
    role: str = "user",
    metadata_username: Optional[str] = None,
) -> int:
    now = _utc_now()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, metadata_username, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, password_hash, role, metadata_username, now),
        )
        return cur.lastrowid


def get_conversation_state(session_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT entity_shortname, location_key, variable, timeframe FROM conversation_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return {
                "entity_shortname": None,
                "location_key": None,
                "variable": None,
                "timeframe": None,
            }
        return dict(row)


def upsert_conversation_state(session_id: str, state: Dict[str, Any]) -> None:
    now = _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO conversation_state (session_id, entity_shortname, location_key, variable, timeframe, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                entity_shortname = excluded.entity_shortname,
                location_key = excluded.location_key,
                variable = excluded.variable,
                timeframe = excluded.timeframe,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                state.get("entity_shortname"),
                state.get("location_key"),
                state.get("variable"),
                state.get("timeframe"),
                now,
            ),
        )


def clear_conversation_state(session_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM conversation_state WHERE session_id = ?", (session_id,))


def log_query_envelope(
    user_id: int,
    request_id: str,
    question: str,
    envelope_json: dict,
    session_id: Optional[str] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO query_log (user_id, request_id, session_id, question, envelope_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, request_id, session_id, question, json.dumps(envelope_json), _utc_now()),
        )


def log_llm_audit_index(request_id: str, log_file_path: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_audit_index (request_id, log_file_path, created_at) VALUES (?, ?, ?)",
            (request_id, log_file_path, _utc_now()),
        )


def get_llm_audit_path(request_id: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT log_file_path FROM llm_audit_index WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return row[0] if row else None
