"""SQLite persistence for v3 planner sessions — separate from v1 and v2 tables."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.app_db import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS planner_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planner_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                envelope_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES planner_sessions(session_id)
            );
            """
        )


def touch_session(session_id: str, user_id: int) -> bool:
    now = _utc_now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM planner_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None and int(row["user_id"]) != int(user_id):
            return False
        conn.execute(
            """
            INSERT INTO planner_sessions
                (session_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (session_id, user_id, now, now),
        )
    return True


def add_turn(
    session_id: str,
    role: str,
    content: str,
    envelope: Optional[Dict[str, Any]] = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO planner_turns (session_id, role, content, envelope_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                content,
                json.dumps(envelope) if envelope is not None else None,
                _utc_now(),
            ),
        )
        conn.execute(
            "UPDATE planner_sessions SET updated_at = ? WHERE session_id = ?",
            (_utc_now(), session_id),
        )


def get_history(session_id: str) -> List[Dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM planner_turns
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def list_sessions(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT s.session_id, s.title, s.updated_at,
                   (SELECT COUNT(*) FROM planner_turns t WHERE t.session_id = s.session_id) AS turn_count
            FROM planner_sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    items = []
    for row in rows:
        title = row["title"] or "Untitled"
        items.append(
            {
                "session_id": row["session_id"],
                "title": title,
                "title_editable": True,
                "updated_at": row["updated_at"],
                "turn_count": int(row["turn_count"] or 0),
            }
        )
    return items


def get_thread(user_id: int, session_id: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        owner = conn.execute(
            "SELECT user_id FROM planner_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if owner is None or int(owner["user_id"]) != int(user_id):
            return []
        rows = conn.execute(
            """
            SELECT role, content, envelope_json, created_at
            FROM planner_turns
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    turns = []
    for row in rows:
        env = None
        if row["envelope_json"]:
            try:
                env = json.loads(row["envelope_json"])
            except json.JSONDecodeError:
                env = None
        turns.append(
            {
                "role": row["role"],
                "content": row["content"],
                "envelope": env,
                "created_at": row["created_at"],
            }
        )
    return turns


def get_title(user_id: int, session_id: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT title FROM planner_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
    if not row:
        return ""
    return row["title"] or "Untitled"


def update_title(user_id: int, session_id: str, title: str) -> Optional[Dict[str, Any]]:
    text = (title or "").strip()
    if not text:
        return None
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE planner_sessions SET title = ?, updated_at = ?
            WHERE session_id = ? AND user_id = ?
            """,
            (text[:200], _utc_now(), session_id, user_id),
        )
        if cur.rowcount == 0:
            return None
    items = [i for i in list_sessions(user_id, 500) if i["session_id"] == session_id]
    return items[0] if items else None


def delete_session(user_id: int, session_id: str) -> bool:
    with get_db() as conn:
        owner = conn.execute(
            "SELECT user_id FROM planner_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if owner is None or int(owner["user_id"]) != int(user_id):
            return False
        conn.execute("DELETE FROM planner_turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM planner_sessions WHERE session_id = ?", (session_id,))
    return True
