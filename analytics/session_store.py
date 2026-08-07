"""SQLite persistence for analytics consult sessions, turns, and REPs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.app_db import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analytics_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analytics_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                aep_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES analytics_sessions(session_id)
            );

            CREATE TABLE IF NOT EXISTS analytics_reps (
                rep_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                aep_json TEXT NOT NULL,
                rep_json TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES analytics_sessions(session_id)
            );
            """
        )


def touch_session(session_id: str, user_id: int) -> None:
    now = _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO analytics_sessions (session_id, user_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                updated_at = excluded.updated_at
            """,
            (session_id, user_id, now),
        )


def add_turn(
    session_id: str,
    role: str,
    content: str,
    aep: Optional[Dict[str, Any]] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO analytics_turns (session_id, role, content, aep_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content,
                json.dumps(aep) if aep is not None else None,
                _utc_now(),
            ),
        )
        conn.execute(
            "UPDATE analytics_sessions SET updated_at = ? WHERE session_id = ?",
            (_utc_now(), session_id),
        )


def get_history(session_id: str, limit: int = 20) -> List[Dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM analytics_turns
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    turns = [{"role": r["role"], "content": r["content"]} for r in rows]
    return turns[-limit:]


def save_pending_rep(
    session_id: str,
    aep: Dict[str, Any],
    rep: Dict[str, Any],
    summary: Dict[str, Any],
) -> str:
    rep_id = uuid.uuid4().hex
    now = _utc_now()
    with get_db() as conn:
        # Reject any prior pending reps for this session
        conn.execute(
            """
            UPDATE analytics_reps
            SET status = 'rejected', updated_at = ?
            WHERE session_id = ? AND status = 'pending'
            """,
            (now, session_id),
        )
        conn.execute(
            """
            INSERT INTO analytics_reps
                (rep_id, session_id, aep_json, rep_json, summary_json, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                rep_id,
                session_id,
                json.dumps(aep),
                json.dumps(rep),
                json.dumps(summary),
                now,
                now,
            ),
        )
    return rep_id


def get_rep(rep_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM analytics_reps WHERE rep_id = ?",
            (rep_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "rep_id": row["rep_id"],
        "session_id": row["session_id"],
        "aep": json.loads(row["aep_json"]),
        "rep": json.loads(row["rep_json"]),
        "summary": json.loads(row["summary_json"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def set_rep_status(rep_id: str, status: str) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE analytics_reps
            SET status = ?, updated_at = ?
            WHERE rep_id = ?
            """,
            (status, _utc_now(), rep_id),
        )
        return cur.rowcount > 0
