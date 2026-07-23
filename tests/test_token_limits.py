"""Tests for token limits, usage aggregation, and enforcement."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth
from config import settings as cfg_settings
from data import app_db, token_limits


def _use_tmp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(app_db, "settings", replace(app_db.settings, app_db_path=db_path))
    app_db.init_db()
    return db_path


def _save_turn(user_id: int, question: str, inp: int, out: int, request_time: str, request_id: str | None = None) -> None:
    payload = {
        "request_id": request_id or f"req_{question.replace(' ', '_')}",
        "session_id": "sess_test",
        "request_time": request_time,
        "response_time": request_time,
        "question": question,
        "answer_type": "Awareness",
        "answer": "ok",
        "llm_usage": {
            "model_id": "test-model",
            "input_tokens": inp,
            "output_tokens": out,
        },
    }
    app_db.save_query_history(user_id, payload)


class TestCycleWindow:
    def test_anchor_mid_month(self):
        start, end = token_limits.get_cycle_window(
            "2026-01-15",
            datetime(2026, 2, 10, tzinfo=timezone.utc),
        )
        assert start.date() == date(2026, 1, 15)
        assert end.date() == date(2026, 2, 15)

    def test_rolls_to_next_cycle(self):
        start, end = token_limits.get_cycle_window(
            "2026-01-15",
            datetime(2026, 2, 20, tzinfo=timezone.utc),
        )
        assert start.date() == date(2026, 2, 15)
        assert end.date() == date(2026, 3, 15)

    def test_anchor_day_31_short_month(self):
        start, end = token_limits.get_cycle_window(
            "2026-01-31",
            datetime(2026, 3, 15, tzinfo=timezone.utc),
        )
        assert start.date() == date(2026, 2, 28)
        assert end.date() == date(2026, 3, 31)


class TestUsageAndLimits:
    def test_pending_user_blocked(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("pending@test.com", "hash", status="pending_limit")
        allowed, msg, summary = app_db.check_token_limit(user_id)
        assert not allowed
        assert "pending" in msg.lower()
        assert summary is None

    def test_active_user_without_limit_blocked(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("nolimit@test.com", "hash", status="active")
        allowed, msg, _ = app_db.check_token_limit(user_id)
        assert not allowed

    def test_usage_tracks_input_output_separately(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("usage@test.com", "hash", status="active")
        app_db.set_user_token_limit(user_id, 100_000, date.today().isoformat())
        ts = datetime.now(timezone.utc).isoformat()
        _save_turn(user_id, "Q1", 100, 50, ts)
        _save_turn(user_id, "Q2", 200, 80, ts)

        summary = app_db.build_usage_summary(user_id)
        assert summary is not None
        assert summary["used_input_tokens"] == 300
        assert summary["used_output_tokens"] == 130
        assert summary["used_tokens"] == 430
        assert summary["remaining_tokens"] == 100_000 - 430

    def test_limit_enforcement_uses_combined_total(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("limit@test.com", "hash", status="active")
        app_db.set_user_token_limit(user_id, 400, date.today().isoformat())
        ts = datetime.now(timezone.utc).isoformat()
        _save_turn(user_id, "Q1", 300, 150, ts)

        allowed, msg, summary = app_db.check_token_limit(user_id)
        assert not allowed
        assert summary["used_tokens"] == 450

    def test_increase_bonus_expands_effective_limit(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("bonus@test.com", "hash", status="active")
        app_db.set_user_token_limit(user_id, 1000, date.today().isoformat())
        app_db.increase_user_token_limit(user_id, 500)
        summary = app_db.build_usage_summary(user_id)
        assert summary["effective_limit"] == 1500
        assert summary["bonus_tokens"] == 500

    def test_breakdown_by_question(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        user_id = app_db.create_user("break@test.com", "hash", status="active")
        app_db.set_user_token_limit(user_id, 100_000, date.today().isoformat())
        ts = datetime.now(timezone.utc).isoformat()
        _save_turn(user_id, "Question A", 10, 5, ts)
        _save_turn(user_id, "Question B", 20, 10, ts)

        rows = app_db.get_usage_breakdown(user_id, "question")
        assert len(rows) == 2
        labels = {r["label"] for r in rows}
        assert "Question A" in labels
        assert "Question B" in labels


class TestRegisterAndQueryAPI:
    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        _use_tmp_db(tmp_path, monkeypatch)
        import app.deps as deps_module

        patched = replace(
            cfg_settings.settings,
            auth=replace(cfg_settings.settings.auth, auth_required=True),
        )
        monkeypatch.setattr(app_db, "settings", replace(patched, app_db_path=tmp_path / "app.db"))
        monkeypatch.setattr(deps_module, "settings", patched)
        from app.main import create_app

        return TestClient(create_app())

    def test_register_creates_pending_user(self, client):
        res = client.post(
            "/api/register",
            json={"email": "newuser@test.com", "password": "password123"},
        )
        assert res.status_code == 200
        user = app_db.get_user_by_email("newuser@test.com")
        assert user is not None
        assert user["status"] == "pending_limit"

    def test_query_blocked_for_pending_user(self, client):
        client.post(
            "/api/register",
            json={"email": "blocked@test.com", "password": "password123"},
        )
        login = client.post(
            "/api/login",
            json={"email": "blocked@test.com", "password": "password123"},
        )
        token = login.json()["access_token"]
        res = client.post(
            "/api/query",
            headers={"Authorization": f"Bearer {token}"},
            json={"question": "hello"},
        )
        assert res.status_code == 403

    def test_admin_can_set_limit(self, client, tmp_path, monkeypatch):
        user_id = app_db.create_user("member@test.com", "hash", status="pending_limit")
        app_db.create_user(
            "admin@test.com",
            auth.hash_password("adminpass"),
            role="admin",
        )

        admin_login = client.post(
            "/api/login",
            json={"email": "admin@test.com", "password": "adminpass"},
        )
        admin_token = admin_login.json()["access_token"]

        res = client.patch(
            f"/api/admin/users/{user_id}/limit",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"base_monthly_limit": 50000},
        )
        assert res.status_code == 200
        user = app_db.get_user_by_id(user_id)
        assert user["status"] == "active"
        limit = app_db.get_user_token_limit(user_id)
        assert limit["base_monthly_limit"] == 50000
