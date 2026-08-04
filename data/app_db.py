"""App DB — users, conversation state, query log, LLM audit index (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from config.settings import settings
from data import token_limits

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
    "original_question",
    "answer_type",
    "assumption",
    "answer",
    "chart_applicable",
    "chart_details",
    "timezone",
    "result_summary",
    "context_warnings",
    "llm_usage",
)

UsageGranularity = Literal["summary", "question", "day", "week", "month"]


def display_question(payload: Dict[str, Any]) -> str:
    """User-facing question text; prefer original_question over LLM reformulation."""
    original = (payload.get("original_question") or "").strip()
    if original:
        return original
    return (payload.get("question") or "").strip()


# Prefer original_question in envelope_json when present (backward compatible).
_DISPLAY_QUESTION_EXPR = (
    "COALESCE("
    "NULLIF(json_extract(envelope_json, '$.original_question'), ''), "
    "question)"
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
    if "input_tokens" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN input_tokens INTEGER")
    if "output_tokens" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN output_tokens INTEGER")
    if "total_tokens" not in cols:
        conn.execute("ALTER TABLE query_log ADD COLUMN total_tokens INTEGER")


def _ensure_users_columns(conn: sqlite3.Connection) -> None:
    cols = _table_columns(conn, "users")
    if "status" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")


def _backfill_token_columns(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, envelope_json FROM query_log
        WHERE total_tokens IS NULL OR input_tokens IS NULL OR output_tokens IS NULL
        """
    ).fetchall()
    for row in rows:
        inp, out, total = _tokens_from_envelope(row["envelope_json"])
        conn.execute(
            "UPDATE query_log SET input_tokens = ?, output_tokens = ?, total_tokens = ? WHERE id = ?",
            (inp, out, total, row["id"]),
        )


def _tokens_from_envelope(envelope_json: str) -> Tuple[int, int, int]:
    try:
        payload = json.loads(envelope_json)
    except (TypeError, json.JSONDecodeError):
        return 0, 0, 0
    usage = payload.get("llm_usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return inp, out, inp + out


def _tokens_from_payload(payload: Dict[str, Any]) -> Tuple[int, int, int]:
    usage = payload.get("llm_usage") or {}
    if not isinstance(usage, dict):
        return 0, 0, 0
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    return inp, out, inp + out


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
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
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
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
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

            CREATE TABLE IF NOT EXISTS user_token_limits (
                user_id INTEGER PRIMARY KEY,
                base_monthly_limit INTEGER NOT NULL,
                cycle_bonus_tokens INTEGER NOT NULL DEFAULT 0,
                cycle_anchor_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            """
        )
        _ensure_users_columns(conn)
        _ensure_query_log_columns(conn)
        _backfill_conversation_sessions(conn)
        _backfill_token_columns(conn)


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
    status: str = "active",
) -> int:
    now = _utc_now()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, role, metadata_username, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email, password_hash, role, metadata_username, now, status),
        )
        return cur.lastrowid


def list_users() -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, email, role, metadata_username, created_at, status FROM users ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def set_user_status(user_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))


def get_user_token_limit(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_token_limits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def set_user_token_limit(user_id: int, base_monthly_limit: int, cycle_anchor_date: str) -> Dict[str, Any]:
    now = _utc_now()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT user_id FROM user_token_limits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE user_token_limits
                SET base_monthly_limit = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (base_monthly_limit, now, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO user_token_limits (
                    user_id, base_monthly_limit, cycle_bonus_tokens,
                    cycle_anchor_date, created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?)
                """,
                (user_id, base_monthly_limit, cycle_anchor_date, now, now),
            )
        conn.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
    row = get_user_token_limit(user_id)
    assert row is not None
    return row


def increase_user_token_limit(user_id: int, bonus_tokens: int) -> Optional[Dict[str, Any]]:
    if bonus_tokens <= 0:
        return get_user_token_limit(user_id)
    now = _utc_now()
    with get_db() as conn:
        row = conn.execute(
            "SELECT cycle_bonus_tokens FROM user_token_limits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        new_bonus = int(row["cycle_bonus_tokens"]) + bonus_tokens
        conn.execute(
            """
            UPDATE user_token_limits
            SET cycle_bonus_tokens = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (new_bonus, now, user_id),
        )
    return get_user_token_limit(user_id)


def _maybe_reset_cycle_bonus(limit_row: Dict[str, Any]) -> Dict[str, Any]:
    """Reset cycle_bonus_tokens when a new cycle has started since last update."""
    cycle_start, _ = token_limits.get_cycle_window(limit_row["cycle_anchor_date"])
    updated_at = limit_row.get("updated_at") or limit_row.get("created_at") or ""
    try:
        updated_dt = datetime.fromisoformat(updated_at)
        if updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return limit_row

    if updated_dt < cycle_start and int(limit_row.get("cycle_bonus_tokens") or 0) > 0:
        now = _utc_now()
        with get_db() as conn:
            conn.execute(
                """
                UPDATE user_token_limits
                SET cycle_bonus_tokens = 0, updated_at = ?
                WHERE user_id = ?
                """,
                (now, limit_row["user_id"]),
            )
        refreshed = get_user_token_limit(limit_row["user_id"])
        return refreshed if refreshed else limit_row
    return limit_row


def get_cycle_usage_totals(
    user_id: int,
    cycle_start: datetime,
    cycle_end: datetime,
) -> Dict[str, int]:
    start_iso = cycle_start.isoformat()
    end_iso = cycle_end.isoformat()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COUNT(*) AS query_count
            FROM query_log
            WHERE user_id = ?
              AND COALESCE(request_time, created_at) >= ?
              AND COALESCE(request_time, created_at) < ?
            """,
            (user_id, start_iso, end_iso),
        ).fetchone()
        return {
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "total_tokens": int(row["total_tokens"] or 0),
            "query_count": int(row["query_count"] or 0),
        }


def build_usage_summary(user_id: int) -> Optional[Dict[str, Any]]:
    limit_row = get_user_token_limit(user_id)
    if not limit_row:
        return None
    limit_row = _maybe_reset_cycle_bonus(limit_row)
    cycle_start, cycle_end = token_limits.get_cycle_window(limit_row["cycle_anchor_date"])
    usage = get_cycle_usage_totals(user_id, cycle_start, cycle_end)
    effective = token_limits.effective_limit(
        limit_row["base_monthly_limit"],
        limit_row["cycle_bonus_tokens"],
    )
    used_total = usage["total_tokens"]
    return {
        "cycle_start": cycle_start.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "base_limit": int(limit_row["base_monthly_limit"]),
        "bonus_tokens": int(limit_row["cycle_bonus_tokens"]),
        "effective_limit": effective,
        "used_input_tokens": usage["input_tokens"],
        "used_output_tokens": usage["output_tokens"],
        "used_tokens": used_total,
        "remaining_tokens": token_limits.remaining_tokens(effective, used_total),
        "query_count": usage["query_count"],
        "cycle_anchor_date": limit_row["cycle_anchor_date"],
    }


def get_usage_breakdown(
    user_id: int,
    granularity: UsageGranularity,
    *,
    cycle_only: bool = True,
) -> List[Dict[str, Any]]:
    limit_row = get_user_token_limit(user_id)
    params: List[Any] = [user_id]
    time_filter = ""
    if cycle_only and limit_row:
        limit_row = _maybe_reset_cycle_bonus(limit_row)
        cycle_start, cycle_end = token_limits.get_cycle_window(limit_row["cycle_anchor_date"])
        time_filter = "AND COALESCE(request_time, created_at) >= ? AND COALESCE(request_time, created_at) < ?"
        params.extend([cycle_start.isoformat(), cycle_end.isoformat()])

    if granularity == "question":
        group_expr = "request_id"
        label_expr = _DISPLAY_QUESTION_EXPR
    elif granularity == "day":
        group_expr = "strftime('%Y-%m-%d', COALESCE(request_time, created_at))"
        label_expr = group_expr
    elif granularity == "week":
        group_expr = "strftime('%Y-W%W', COALESCE(request_time, created_at))"
        label_expr = group_expr
    elif granularity == "month":
        group_expr = "strftime('%Y-%m', COALESCE(request_time, created_at))"
        label_expr = group_expr
    else:
        return []

    sql = f"""
        SELECT
            {label_expr} AS label,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COUNT(*) AS query_count
        FROM query_log
        WHERE user_id = ? {time_filter}
        GROUP BY {group_expr}
        ORDER BY MAX(COALESCE(request_time, created_at)) DESC
    """
    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "label": row["label"] or "",
                "input_tokens": int(row["input_tokens"] or 0),
                "output_tokens": int(row["output_tokens"] or 0),
                "total_tokens": int(row["total_tokens"] or 0),
                "query_count": int(row["query_count"] or 0),
            }
            for row in rows
        ]


def check_token_limit(user_id: int) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Return (allowed, error_message, usage_summary).
    Admins should be checked before calling this.
    """
    user = get_user_by_id(user_id)
    if not user:
        return False, "User not found", None
    status = user.get("status") or "active"
    if status == "pending_limit":
        return False, "Account pending — admin must set your monthly token limit", None

    limit_row = get_user_token_limit(user_id)
    if not limit_row:
        return False, "Account pending — admin must set your monthly token limit", None

    summary = build_usage_summary(user_id)
    if not summary:
        return False, "Could not load token limit", None

    if summary["used_tokens"] >= summary["effective_limit"]:
        return False, "Monthly token limit exceeded for the current cycle", summary

    return True, None, summary


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
    inp, out, total = _tokens_from_payload(payload)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO query_log (
                user_id, request_id, session_id, question, envelope_json,
                created_at, request_time, response_time, answer_type,
                input_tokens, output_tokens, total_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload.get("request_id"),
                session_id,
                display_question(payload),
                json.dumps(payload),
                _utc_now(),
                request_time,
                payload.get("response_time"),
                payload.get("answer_type"),
                inp,
                out,
                total,
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
        f"""
        SELECT {_DISPLAY_QUESTION_EXPR} AS question
        FROM query_log
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
            f"""
            SELECT
                cs.session_id,
                cs.title AS custom_title,
                cs.updated_at,
                (
                    SELECT COUNT(*) FROM query_log ql
                    WHERE ql.user_id = cs.user_id AND ql.session_id = cs.session_id
                ) AS turn_count,
                (
                    SELECT { _DISPLAY_QUESTION_EXPR } FROM query_log ql
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
