"""SQLite persistence for analytics consult sessions, turns, and REPs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data.app_db import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def ensure_tables() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analytics_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
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
        # Migrate older installs that only had (session_id, user_id, updated_at).
        cols = _table_columns(conn, "analytics_sessions")
        if "title" not in cols:
            conn.execute("ALTER TABLE analytics_sessions ADD COLUMN title TEXT")
        if "created_at" not in cols:
            conn.execute(
                "ALTER TABLE analytics_sessions ADD COLUMN created_at TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                """
                UPDATE analytics_sessions
                SET created_at = updated_at
                WHERE created_at IS NULL OR created_at = ''
                """
            )


def touch_session(session_id: str, user_id: int) -> bool:
    """Create or refresh a session. False when another user already owns it."""
    now = _utc_now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM analytics_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None and int(row["user_id"]) != int(user_id):
            return False
        conn.execute(
            """
            INSERT INTO analytics_sessions
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
    aep: Optional[Dict[str, Any]] = None,
) -> None:
    now = _utc_now()
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
                now,
            ),
        )
        # Auto-title from the first user message when unset.
        if role == "user" and (content or "").strip():
            conn.execute(
                """
                UPDATE analytics_sessions
                SET title = ?, updated_at = ?
                WHERE session_id = ? AND (title IS NULL OR TRIM(title) = '')
                """,
                (content.strip()[:200], now, session_id),
            )
        else:
            conn.execute(
                "UPDATE analytics_sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
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


def get_thread(session_id: str, user_id: int) -> Optional[List[Dict[str, Any]]]:
    """Full turn list for UI hydration, or None if the session is missing/unowned."""
    with get_db() as conn:
        owner = conn.execute(
            "SELECT user_id FROM analytics_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not owner or int(owner["user_id"]) != int(user_id):
            return None
        rows = conn.execute(
            """
            SELECT id, role, content, aep_json, created_at
            FROM analytics_turns
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        aep = None
        if row["aep_json"]:
            try:
                aep = json.loads(row["aep_json"])
            except json.JSONDecodeError:
                aep = None
        out.append(
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "aep": aep,
                "created_at": row["created_at"],
            }
        )
    return out


def list_sessions(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                s.session_id,
                s.title,
                s.updated_at,
                (
                    SELECT COUNT(*) FROM analytics_turns t
                    WHERE t.session_id = s.session_id AND t.role = 'user'
                ) AS turn_count,
                (
                    SELECT t.content FROM analytics_turns t
                    WHERE t.session_id = s.session_id AND t.role = 'user'
                    ORDER BY t.id ASC
                    LIMIT 1
                ) AS first_question
            FROM analytics_sessions s
            WHERE s.user_id = ?
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        custom = row["title"]
        first_q = (row["first_question"] or "").strip()
        # Title is auto-set from first question; treat matching auto-title as editable.
        title = (custom or "").strip() or first_q or "Untitled conversation"
        title_editable = not custom or custom.strip() == first_q
        items.append(
            {
                "session_id": row["session_id"],
                "title": title,
                "title_editable": title_editable,
                "updated_at": row["updated_at"],
                "turn_count": int(row["turn_count"] or 0),
            }
        )
    return items


def get_session_item(user_id: int, session_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT session_id, title, updated_at
            FROM analytics_sessions
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()
        if not row:
            return None
        turn_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM analytics_turns
            WHERE session_id = ? AND role = 'user'
            """,
            (session_id,),
        ).fetchone()["c"]
        first = conn.execute(
            """
            SELECT content FROM analytics_turns
            WHERE session_id = ? AND role = 'user'
            ORDER BY id ASC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    first_q = ((first["content"] if first else "") or "").strip()
    custom = row["title"]
    title = (custom or "").strip() or first_q or "Untitled conversation"
    title_editable = not custom or custom.strip() == first_q
    return {
        "session_id": row["session_id"],
        "title": title,
        "title_editable": title_editable,
        "updated_at": row["updated_at"],
        "turn_count": int(turn_count or 0),
    }


def update_session_title(
    user_id: int, session_id: str, title: str
) -> Optional[Dict[str, Any]]:
    trimmed = (title or "").strip()
    if not trimmed:
        return None
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE analytics_sessions
            SET title = ?, updated_at = ?
            WHERE user_id = ? AND session_id = ?
            """,
            (trimmed[:200], _utc_now(), user_id, session_id),
        )
        if cur.rowcount == 0:
            return None
    return get_session_item(user_id, session_id)


def delete_session(user_id: int, session_id: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM analytics_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM analytics_reps WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM analytics_turns WHERE session_id = ?", (session_id,))
        conn.execute(
            "DELETE FROM analytics_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
    return True


def get_pending_rep_for_session(
    session_id: str, user_id: int
) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT r.*, s.user_id AS owner_id
            FROM analytics_reps r
            JOIN analytics_sessions s ON s.session_id = r.session_id
            WHERE r.session_id = ? AND r.status = 'pending'
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
    if not row:
        return None
    if int(row["owner_id"]) != int(user_id):
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


def get_rep(rep_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Load a REP. When user_id is given, only the owning user's REP is returned."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT r.*, s.user_id AS owner_id
            FROM analytics_reps r
            JOIN analytics_sessions s ON s.session_id = r.session_id
            WHERE r.rep_id = ?
            """,
            (rep_id,),
        ).fetchone()
    if not row:
        return None
    if user_id is not None and int(row["owner_id"]) != int(user_id):
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
