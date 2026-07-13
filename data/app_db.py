"""App DB — users, conversation state, query log, LLM audit index (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Fields stored in query history JSON (QueryResponse minus data).
HISTORY_PAYLOAD_KEYS = (
    "request_id",
    "session_id",
    "request_time",
    "response_time",
    "clarity_required",
    "clarifying_question",
    "question",
    "answer_type",
    "assumption",
    "answer",
    "chart_applicable",
    "chart_details",
    "timezone",
    "context_warnings",
)


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


def _table_columns(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _ensure_query_log_columns(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "query_log")
    if "request_time" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN request_time TEXT")
    if "response_time" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN response_time TEXT")
    if "answer_type" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN answer_type TEXT")


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
                request_time TEXT,
                response_time TEXT,
                answer_type TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS conversation_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS llm_audit_index (
                request_id TEXT PRIMARY KEY,
                log_file_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_query_log_columns(conn)
        _backfill_conversation_sessions(conn)


def _backfill_conversation_sessions(conn: sqlite3.Connection) -> None:
    """Populate conversation_sessions from existing query_log rows."""
    conn.execute(
        """
        INSERT OR IGNORE INTO conversation_sessions (session_id, user_id, title, created_at, updated_at)
        SELECT
            session_id,
            user_id,
            NULL,
            MIN(COALESCE(request_time, created_at)),
            MAX(COALESCE(request_time, created_at))
        FROM query_log
        WHERE session_id IS NOT NULL AND session_id != ''
        GROUP BY session_id, user_id
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


def history_payload_from_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Build the stored history object (QueryResponse fields minus data)."""
    return {key: response.get(key) for key in HISTORY_PAYLOAD_KEYS}


def _upsert_conversation_session(
    conn: sqlite3.Connection,
    user_id: int,
    session_id: str,
    request_time: str,
) -> None:
    if not session_id:
        return
    ts = request_time or _utc_now()
    conn.execute(
        """
        INSERT INTO conversation_sessions (session_id, user_id, title, created_at, updated_at)
        VALUES (?, ?, NULL, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            updated_at = excluded.updated_at
        """,
        (session_id, user_id, ts, ts),
    )


def save_query_history(user_id: int, payload: Dict[str, Any]) -> None:
    """Persist a query history row. `payload` must be the history object (no data)."""
    session_id = payload.get("session_id") or ""
    request_time = payload.get("request_time") or _utc_now()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO query_log (
                user_id, request_id, session_id, question, envelope_json,
                created_at, request_time, response_time, answer_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload.get("request_id"),
                session_id,
                payload.get("question") or "",
                json.dumps(payload),
                _utc_now(),
                request_time,
                payload.get("response_time"),
                payload.get("answer_type"),
            ),
        )
        _upsert_conversation_session(conn, user_id, session_id, request_time)


def log_query_envelope(
    user_id: int,
    request_id: str,
    question: str,
    envelope_json: dict,
    session_id: Optional[str] = None,
) -> None:
    """Deprecated path kept for compatibility; prefer save_query_history."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO query_log (user_id, request_id, session_id, question, envelope_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, request_id, session_id, question, json.dumps(envelope_json), _utc_now()),
        )


def _first_turn_question(conn: sqlite3.Connection, user_id: int, session_id: str) -> str:
    row = conn.execute(
        """
        SELECT question FROM query_log
        WHERE user_id = ? AND session_id = ?
        ORDER BY COALESCE(request_time, created_at) ASC
        LIMIT 1
        """,
        (user_id, session_id),
    ).fetchone()
    return (row["question"] or "") if row else ""


def list_conversation_sessions(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                cs.session_id,
                cs.title AS custom_title,
                cs.updated_at,
                (
                    SELECT COUNT(*) FROM query_log ql
                    WHERE ql.user_id = cs.user_id AND ql.session_id = cs.session_id
                ) AS turn_count,
                (
                    SELECT ql.question FROM query_log ql
                    WHERE ql.user_id = cs.user_id AND ql.session_id = cs.session_id
                    ORDER BY COALESCE(ql.request_time, ql.created_at) ASC
                    LIMIT 1
                ) AS first_question
            FROM conversation_sessions cs
            WHERE cs.user_id = ?
            ORDER BY cs.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            custom = row["custom_title"]
            first_q = row["first_question"] or ""
            title = custom if custom else first_q
            out.append(
                {
                    "session_id": row["session_id"],
                    "title": title or "Untitled conversation",
                    "title_editable": custom is None,
                    "updated_at": row["updated_at"],
                    "turn_count": row["turn_count"] or 0,
                }
            )
        return out


def get_conversation_session_item(user_id: int, session_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT session_id, title AS custom_title, updated_at
            FROM conversation_sessions
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()
        if not row:
            return None
        turn_count = conn.execute(
            "SELECT COUNT(*) AS c FROM query_log WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()["c"]
        first_q = _first_turn_question(conn, user_id, session_id)
        custom = row["custom_title"]
        title = custom if custom else first_q
        return {
            "session_id": row["session_id"],
            "title": title or "Untitled conversation",
            "title_editable": custom is None,
            "updated_at": row["updated_at"],
            "turn_count": turn_count or 0,
        }


def update_session_title(user_id: int, session_id: str, title: str) -> Optional[Dict[str, Any]]:
    trimmed = title.strip()
    if not trimmed:
        return None
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE conversation_sessions
            SET title = ?
            WHERE user_id = ? AND session_id = ?
            """,
            (trimmed, user_id, session_id),
        )
        if cur.rowcount == 0:
            return None
    return get_conversation_session_item(user_id, session_id)


def delete_conversation_session(user_id: int, session_id: str) -> bool:
    """Delete a conversation and all its query_log turns for this user."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversation_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            "DELETE FROM query_log WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
        conn.execute(
            "DELETE FROM conversation_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
        conn.execute(
            "DELETE FROM conversation_state WHERE session_id = ?",
            (session_id,),
        )
    return True


def get_session_thread(user_id: int, session_id: str) -> List[Dict[str, Any]]:
    """Return all stored history payloads for a session, ordered by request_time ASC."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT envelope_json
            FROM query_log
            WHERE user_id = ? AND session_id = ?
            ORDER BY COALESCE(request_time, created_at) ASC
            """,
            (user_id, session_id),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["envelope_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                out.append(payload)
        return out


def get_session_display_title(user_id: int, session_id: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT title FROM conversation_sessions WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
        if row and row["title"]:
            return row["title"]
        return _first_turn_question(conn, user_id, session_id) or "Untitled conversation"


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
