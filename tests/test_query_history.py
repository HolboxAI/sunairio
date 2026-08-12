"""Tests for query history persistence."""

from __future__ import annotations

import json
from dataclasses import replace

from data import app_db


def _use_tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(app_db, "settings", replace(app_db.settings, app_db_path=db_path))
    app_db.init_db()
    return db_path


def _sample_payload(**overrides):
    base = {
        "request_id": "req-1",
        "session_id": "session_a",
        "request_time": "2026-07-12T06:26:08.159954+00:00",
        "response_time": "2026-07-12T06:26:21.793313+00:00",
        "clarity_required": False,
        "clarifying_question": None,
        "question": "P50 solar next week?",
        "original_question": "P50 solar next week?",
        "answer_type": "Sql",
        "assumption": ["Entity: ercot"],
        "answer": "SELECT 1",
        "chart_applicable": True,
        "chart_details": {"chart_type": "line", "x_axis": ["t"], "y_axis": ["v"]},
        "context_warnings": [],
    }
    base.update(overrides)
    if "original_question" not in overrides:
        base["original_question"] = base["question"]
    return base


def test_display_question_prefers_original():
    assert app_db.display_question({"original_question": "user q", "question": "llm q"}) == "user q"
    assert app_db.display_question({"question": "llm q"}) == "llm q"
    assert app_db.display_question({}) == ""


def test_save_stores_original_question_in_question_column(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("orig@example.com", "hash")
    app_db.save_query_history(
        user_id,
        _sample_payload(
            question="LLM reformulated question",
            original_question="what the user typed",
        ),
    )

    with app_db.get_db() as conn:
        row = conn.execute("SELECT question FROM query_log").fetchone()
        assert row["question"] == "what the user typed"

    sessions = app_db.list_conversation_sessions(user_id)
    assert sessions[0]["title"] == "what the user typed"


def test_legacy_rows_without_original_question_still_resolve(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("legacy@example.com", "hash")
    payload = _sample_payload(question="Legacy LLM question")
    del payload["original_question"]
    with app_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO query_log (
                user_id, request_id, session_id, question, envelope_json, created_at, request_time
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload["request_id"],
                payload["session_id"],
                payload["question"],
                json.dumps(payload),
                "2026-07-12T06:00:00+00:00",
                "2026-07-12T06:00:00+00:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO conversation_sessions (session_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (
                payload["session_id"],
                user_id,
                "2026-07-12T06:00:00+00:00",
                "2026-07-12T06:00:00+00:00",
            ),
        )

    sessions = app_db.list_conversation_sessions(user_id)
    assert sessions[0]["title"] == "Legacy LLM question"


def test_save_and_list_conversation_sessions(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("hist@example.com", "hash")

    app_db.save_query_history(user_id, _sample_payload())
    app_db.save_query_history(
        user_id,
        _sample_payload(
            request_id="req-2",
            request_time="2026-07-12T06:27:00+00:00",
            question="Follow-up question?",
        ),
    )
    app_db.save_query_history(
        user_id,
        _sample_payload(
            request_id="req-3",
            session_id="session_b",
            request_time="2026-07-12T07:00:00+00:00",
            question="Other session question",
        ),
    )

    sessions = app_db.list_conversation_sessions(user_id)
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "session_b"
    assert sessions[0]["turn_count"] == 1
    assert sessions[1]["session_id"] == "session_a"
    assert sessions[1]["turn_count"] == 2
    assert sessions[1]["title"] == "P50 solar next week?"
    assert sessions[1]["title_editable"] is True


def test_unlinked_usage_not_listed_in_classic_history(tmp_path, monkeypatch):
    """Analytics consult usage logs tokens but must not appear in /chat history."""
    from analytics import session_store

    _use_tmp_db(tmp_path, monkeypatch)
    session_store.ensure_tables()
    user_id = app_db.create_user("analytics-usage@example.com", "hash")
    analytics_sid = "analytics_only_session"

    assert session_store.touch_session(analytics_sid, user_id) is True
    app_db.save_query_history(
        user_id,
        _sample_payload(
            session_id=analytics_sid,
            question="Analytics consult question",
            answer_type="Awareness",
        ),
        link_conversation=False,
    )
    app_db.save_query_history(
        user_id,
        _sample_payload(session_id="classic_session", question="Classic chat question"),
    )

    sessions = app_db.list_conversation_sessions(user_id)
    assert [s["session_id"] for s in sessions] == ["classic_session"]

    # Ready/init backfill must not promote analytics usage into /chat history.
    app_db.init_db()
    sessions_after = app_db.list_conversation_sessions(user_id)
    assert [s["session_id"] for s in sessions_after] == ["classic_session"]


def test_init_db_purges_analytics_sessions_leaked_into_classic_history(tmp_path, monkeypatch):
    from analytics import session_store

    _use_tmp_db(tmp_path, monkeypatch)
    session_store.ensure_tables()
    user_id = app_db.create_user("leaked@example.com", "hash")
    analytics_sid = "leaked_analytics_session"

    assert session_store.touch_session(analytics_sid, user_id) is True
    # Simulate the old bug: analytics usage row + conversation_sessions row.
    app_db.save_query_history(
        user_id,
        _sample_payload(session_id=analytics_sid, question="Should stay on /analytics"),
        link_conversation=False,
    )
    with app_db.get_db() as conn:
        conn.execute(
            """
            INSERT INTO conversation_sessions (session_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (analytics_sid, user_id, "2026-08-10T00:00:00+00:00", "2026-08-10T00:00:00+00:00"),
        )

    app_db.init_db()
    assert app_db.list_conversation_sessions(user_id) == []


def test_default_title_is_first_turn_question(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("title@example.com", "hash")

    app_db.save_query_history(
        user_id,
        _sample_payload(question="First question here", request_time="2026-07-12T06:01:00+00:00"),
    )
    app_db.save_query_history(
        user_id,
        _sample_payload(
            request_id="req-2",
            question="Second question",
            request_time="2026-07-12T06:02:00+00:00",
        ),
    )

    sessions = app_db.list_conversation_sessions(user_id)
    assert sessions[0]["title"] == "First question here"


def test_update_session_title(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("edit@example.com", "hash")
    app_db.save_query_history(user_id, _sample_payload())

    updated = app_db.update_session_title(user_id, "session_a", "  Custom title  ")
    assert updated is not None
    assert updated["title"] == "Custom title"
    assert updated["title_editable"] is False

    sessions = app_db.list_conversation_sessions(user_id)
    assert sessions[0]["title"] == "Custom title"
    assert sessions[0]["title_editable"] is False


def test_get_session_thread_full_session(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("thread@example.com", "hash")

    base = {
        "clarity_required": False,
        "clarifying_question": None,
        "answer_type": "Sql",
        "assumption": [],
        "answer": "SELECT 1",
        "chart_applicable": False,
        "chart_details": None,
        "context_warnings": [],
        "response_time": "2026-07-12T06:00:00+00:00",
        "session_id": "s1",
    }
    turns = [
        {**base, "request_id": "r1", "request_time": "2026-07-12T06:01:00+00:00", "question": "Q1"},
        {**base, "request_id": "r2", "request_time": "2026-07-12T06:02:00+00:00", "question": "Q2"},
        {**base, "request_id": "r3", "request_time": "2026-07-12T06:03:00+00:00", "question": "Q3"},
    ]
    for t in turns:
        app_db.save_query_history(user_id, t)

    thread = app_db.get_session_thread(user_id, "s1")
    assert [t["question"] for t in thread] == ["Q1", "Q2", "Q3"]
    assert "data" not in thread[0]


def test_history_payload_excludes_data():
    response = {
        "request_id": "r",
        "session_id": "s",
        "request_time": "t0",
        "response_time": "t1",
        "clarity_required": False,
        "clarifying_question": None,
        "question": "q",
        "answer_type": "Sql",
        "assumption": [],
        "answer": "SELECT 1",
        "chart_applicable": False,
        "chart_details": None,
        "result_summary": "The probability is 0.1.",
        "data": {"columns": ["a"], "rows": [[1]], "row_count": 1, "query_time_ms": 1, "backend": "forecast"},
        "context_warnings": [],
        "llm_usage": {
            "model_id": "us.anthropic.claude-sonnet-4-6",
            "input_tokens": 100,
            "output_tokens": 50,
        },
    }
    payload = app_db.history_payload_from_response(response)
    assert "data" not in payload
    assert payload["question"] == "q"
    assert payload["result_summary"] == "The probability is 0.1."
    assert payload["llm_usage"]["input_tokens"] == 100


def test_get_session_display_title(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("display@example.com", "hash")
    app_db.save_query_history(user_id, _sample_payload(question="Auto title"))

    assert app_db.get_session_display_title(user_id, "session_a") == "Auto title"
    app_db.update_session_title(user_id, "session_a", "Manual title")
    assert app_db.get_session_display_title(user_id, "session_a") == "Manual title"


def test_delete_conversation_session(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("delete@example.com", "hash")
    app_db.save_query_history(user_id, _sample_payload())
    app_db.save_query_history(
        user_id,
        _sample_payload(request_id="req-2", question="Second turn"),
    )

    assert app_db.delete_conversation_session(user_id, "session_a") is True
    assert app_db.list_conversation_sessions(user_id) == []
    assert app_db.get_session_thread(user_id, "session_a") == []
    assert app_db.delete_conversation_session(user_id, "session_a") is False


def test_hydrate_resumes_session_preserves_conversation_state(tmp_path, monkeypatch):
    from app import sessions
    from app.api.routes_query import hydrate_history_into_session
    from app.api.schemas import HistoryHydrateRequest
    from core import conversation_state
    from core.models import ConversationState

    _use_tmp_db(tmp_path, monkeypatch)
    user_id = app_db.create_user("hydrate@example.com", "hash")
    app_db.save_query_history(user_id, _sample_payload(
        question="LLM first question",
        original_question="First question",
    ))
    app_db.save_query_history(
        user_id,
        _sample_payload(
            request_id="req-2",
            question="LLM second question",
            original_question="Second question",
        ),
    )
    conversation_state.save(
        "session_a",
        ConversationState(
            entity_shortname="ercot_generic",
            location_key="rto",
            variable="load",
            timeframe="latest init",
        ),
    )

    sessions.clear("session_a")
    response = hydrate_history_into_session(
        HistoryHydrateRequest(session_id="session_a"),
        user={"id": user_id},
    )

    assert response.session_id == "session_a"
    assert [app_db.display_question(t) for t in response.turns] == ["First question", "Second question"]
    assert sessions.get_history("session_a") == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "SELECT 1"},
        {"role": "user", "content": "Second question"},
        {"role": "assistant", "content": "SELECT 1"},
    ]
    state = conversation_state.load("session_a")
    assert state.entity_shortname == "ercot_generic"
    assert state.location_key == "rto"
    assert state.variable == "load"
