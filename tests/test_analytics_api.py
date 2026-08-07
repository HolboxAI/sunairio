"""Analytics v2 consult/confirm API tests (mocked LLM1)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from analytics.models import AnalyticalExecutionPlan
from data import app_db


RESOLVED_AEP = AnalyticalExecutionPlan.from_dict(
    {
        "status": "resolved",
        "assistant_message": "Ready for confirmation.",
        "clarification_questions": [],
        "query": {
            "intent": "forecast",
            "analysis_type": "time_series",
            "entity": {"role": "filter", "mode": "explicit", "values": ["ERCOT"]},
            "location": {
                "role": "dimension",
                "mode": "logical_group",
                "values": ["All Load Zones"],
            },
            "variable": {"role": "filter", "mode": "explicit", "values": ["temp_2m"]},
            "timeframe": {"mode": "relative", "expression": "next_week"},
            "initialization": {"role": "filter", "mode": "latest", "values": []},
            "statistics": {"operation": "percentile", "value": 50},
            "visualization": {
                "required": True,
                "chart_type": "line",
                "x_axis": {"meaning": "Forecast Time"},
                "y_axis": [{"meaning": "Temperature", "unit": "°C"}],
                "legend": "Location",
            },
        },
        "notes": [],
    }
)

CLARIFY_AEP = AnalyticalExecutionPlan.from_dict(
    {
        "status": "clarification_required",
        "assistant_message": "Which forecast representation?",
        "clarification_questions": ["Mean, median (P50), or another percentile?"],
        "query": {"intent": "forecast"},
    }
)


def _injection():
    return {
        "current_utc": "2026-08-06T12:00:00Z",
        "allowed_entities": [
            {
                "entity": "ERCOT",
                "shortname": "ercot_generic",
                "timezone": "US/Central",
                "type": "ISO",
            }
        ],
        "variable_catalog": [
            {
                "variable": "temp_2m",
                "display_name": "2 m Air Temperature",
                "aliases": ["temperature"],
                "category": "Weather",
                "unit": "°C",
            }
        ],
        "location_types": {},
        "logical_location_groups": [],
        "_resolver": {
            "allowed_entities": [
                {
                    "entity_id": "1",
                    "entity": "ERCOT",
                    "shortname": "ercot_generic",
                    "timezone": "US/Central",
                }
            ],
            "latest_inits": {
                "ercot_generic": {
                    "weather": {"forecast": "2026-08-05T18:00:00+00:00"},
                    "energy": {},
                    "fundamental_market": {},
                }
            },
            "entity_catalog": {
                "ercot_generic": {
                    "portfolio": None,
                    "resources": [
                        {
                            "resource_name": "Houston",
                            "energy_sims_id": "houston_cdr",
                            "weather_sims_id": "houston",
                            "resource_type": "load",
                            "is_aggregate": True,
                        },
                        {
                            "resource_name": "North",
                            "energy_sims_id": "north_raybn",
                            "weather_sims_id": "north",
                            "resource_type": "load",
                            "is_aggregate": True,
                        },
                    ],
                }
            },
            "variable_catalog": [
                {
                    "variable": "temp_2m",
                    "display_name": "2 m Air Temperature",
                    "aliases": ["temperature"],
                    "category": "Weather",
                    "unit": "°C",
                },
                {
                    "variable": "load",
                    "display_name": "Electric Load",
                    "aliases": ["demand", "load"],
                    "category": "Energy",
                    "unit": "MW",
                },
            ],
        },
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "analytics_test.db"
    import config.settings as cfg_settings
    from app import auth as auth_module
    from app import main as app_main
    from data import pools
    from llm import client as bedrock

    patched = replace(
        cfg_settings.settings,
        app_db_path=db_path,
        analytics_consult_log_dir=tmp_path / "consult-logs",
        auth=replace(cfg_settings.settings.auth, auth_required=True),
    )
    monkeypatch.setattr(cfg_settings, "settings", patched)
    monkeypatch.setattr(app_db, "settings", patched)
    import app.deps as deps_module

    monkeypatch.setattr(deps_module, "settings", patched)
    monkeypatch.setattr(auth_module, "settings", patched)
    import observability.analytics_consult_log as consult_log_module

    monkeypatch.setattr(consult_log_module, "settings", patched)
    app_db.init_db()
    app_db.create_user(
        "admin@test.com",
        auth_module.hash_password("adminpass"),
        role="admin",
    )

    monkeypatch.setattr(pools, "init_all", lambda: None)
    monkeypatch.setattr(pools, "close_all", lambda: None)
    monkeypatch.setattr(bedrock, "init_client", lambda: None)

    with TestClient(app_main.create_app()) as c:
        login = c.post(
            "/api/login",
            json={"email": "admin@test.com", "password": "adminpass"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c


def test_consult_clarify_then_confirm(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    calls = {"n": 0}

    def fake_run(message, injection, history, system_prompt=None):
        calls["n"] += 1
        if calls["n"] == 1:
            aep = CLARIFY_AEP
        else:
            aep = RESOLVED_AEP
        usage = {"input_tokens": 10, "output_tokens": 20, "model_id": "test-model"}
        return aep, json.dumps(aep.to_dict()), usage

    monkeypatch.setattr(llm1_agent, "run_llm1", fake_run)
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r1 = client.post(
        "/api/v2/consult",
        json={"message": "Temperature next week", "session_id": "s1"},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["phase"] == "clarify"
    assert body1["questions"]

    r2 = client.post(
        "/api/v2/consult",
        json={"message": "Use P50", "session_id": "s1"},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["phase"] == "confirm"
    assert body2["rep_id"]
    assert body2["summary"]["entity"] == "ERCOT"
    assert "2026-08-05" in body2["summary"]["initialization_resolved"]

    r3 = client.post(
        "/api/v2/confirm",
        json={
            "session_id": "s1",
            "rep_id": body2["rep_id"],
            "action": "confirm",
        },
    )
    assert r3.status_code == 200
    body3 = r3.json()
    assert body3["phase"] == "confirmed"
    assert "Phase 2" in body3["message"]


def test_awareness_returns_answered_without_entity_error(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    awareness = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "You have access to ERCOT, ISONE, MISO, and PJM.",
            "query": {
                "intent": "awareness",
                "entity": {"mode": "metadata_query", "values": []},
                "variable": {"values": []},
                "location": {"mode": "metadata_query", "values": []},
            },
        }
    )
    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            awareness,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r = client.post(
        "/api/v2/consult",
        json={"message": "Which entities do I have?", "session_id": "s_aware"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    assert "ERCOT" in body["assistant_message"]
    assert "Entity is required" not in (body["assistant_message"] or "")
    assert not any("Entity is required" in q for q in (body.get("questions") or []))


def test_metadata_locations_confirm_without_variable(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I'll look up weather locations available for ERCOT.",
            "query": {
                "intent": "metadata",
                "analysis_type": "metadata_lookup",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {
                    "mode": "metadata_query",
                    "values": [],
                    "criteria": {"type_filter": ["wx_zone"]},
                },
                "variable": {"values": []},
                "timeframe": {"mode": "none"},
                "initialization": {"mode": "none"},
                "statistics": {},
                "visualization": {"required": False},
            },
        }
    )
    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            meta,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r = client.post(
        "/api/v2/consult",
        json={"message": "What are the weather locations in ercot", "session_id": "s_meta"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "confirm"
    assert body["rep_id"]
    assert "Variable is required" not in (body["assistant_message"] or "")
    assert body["summary"]["locations"] == "Weather locations"


def test_confirm_reject_returns_clarify(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            RESOLVED_AEP,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r = client.post("/api/v2/consult", json={"message": "temp next week", "session_id": "s2"})
    assert r.status_code == 200
    rep_id = r.json()["rep_id"]

    rejected = client.post(
        "/api/v2/confirm",
        json={"session_id": "s2", "rep_id": rep_id, "action": "reject"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["phase"] == "clarify"


def test_analytics_page_served(client):
    r = client.get("/analytics")
    assert r.status_code == 200
    assert "Analytics" in r.text


def _second_user_client(client):
    """A separate authenticated client backed by the same app + database."""
    from app import auth as auth_module

    app_db.create_user(
        "other@test.com",
        auth_module.hash_password("otherpass"),
        role="admin",
    )
    login = client.post(
        "/api/login",
        json={"email": "other@test.com", "password": "otherpass"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_another_user_cannot_confirm_or_hijack_a_session(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            RESOLVED_AEP,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    owner = client.post(
        "/api/v2/consult",
        json={"message": "temp next week", "session_id": "s_private"},
    )
    assert owner.status_code == 200
    rep_id = owner.json()["rep_id"]

    intruder = _second_user_client(client)

    # Cannot post into someone else's conversation
    hijack = client.post(
        "/api/v2/consult",
        json={"message": "what did they ask?", "session_id": "s_private"},
        headers=intruder,
    )
    assert hijack.status_code == 403

    # Cannot confirm someone else's resolved plan
    steal = client.post(
        "/api/v2/confirm",
        json={"session_id": "s_private", "rep_id": rep_id, "action": "confirm"},
        headers=intruder,
    )
    assert steal.status_code == 404

    # The owner's plan is untouched and still confirmable
    ok = client.post(
        "/api/v2/confirm",
        json={"session_id": "s_private", "rep_id": rep_id, "action": "confirm"},
    )
    assert ok.status_code == 200
    assert ok.json()["phase"] == "confirmed"


def test_historical_consult_does_not_show_forecast_initialization(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    historical = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "Ready.",
            "query": {
                "intent": "historical",
                "analysis_type": "time_series",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {"mode": "explicit", "values": ["Houston"]},
                "variable": {"mode": "explicit", "values": ["load"]},
                "timeframe": {"mode": "relative", "expression": "last_week"},
                "initialization": {"mode": "latest"},
                "statistics": {"operation": "mean"},
                "visualization": {"required": True, "chart_type": "line"},
            },
        }
    )
    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            historical,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r = client.post(
        "/api/v2/consult",
        json={"message": "ERCOT Houston load last week", "session_id": "s_hist"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "confirm"
    assert body["rep_preview"]["initialization"]["mode"] == "none"
    assert body["rep_preview"]["routing"]["historical_database"] is True
    assert body["summary"]["forecast_horizon"] == "2026-07-27 → 2026-08-02"


def test_consult_writes_one_log_file_per_turn(client, monkeypatch, tmp_path):
    """The turn log must hold the LLM1 prompt, its reply, and what the user sees."""
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    raw = json.dumps(RESOLVED_AEP.to_dict())

    def fake_run(message, injection, history, system_prompt=None):
        usage = {
            "input_tokens": 11,
            "output_tokens": 22,
            "model_id": "test-model",
            "validation_errors": [],
            "latency_ms": 42,
            "system_prompt": "SYSTEM PROMPT SENTINEL",
            "assembled_user_message": "USER MESSAGE SENTINEL",
            "history_turns": len(history or []),
        }
        return RESOLVED_AEP, raw, usage

    monkeypatch.setattr(llm1_agent, "run_llm1", fake_run)
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    r = client.post(
        "/api/v2/consult",
        json={"message": "temp next week", "session_id": "s_log"},
    )
    assert r.status_code == 200

    log_dir = tmp_path / "consult-logs"
    files = list(log_dir.glob("*.log"))
    assert len(files) == 1, files
    text = files[0].read_text()

    assert "1. FINAL PROMPT SENT TO LLM1" in text
    assert "SYSTEM PROMPT SENTINEL" in text
    assert "USER MESSAGE SENTINEL" in text

    assert "2. RESPONSE RECEIVED FROM LLM1" in text
    assert "temp_2m" in text

    assert "3. DETERMINISTIC RESOLVER OUTCOME" in text
    assert "2026-08-10" in text

    assert "4. RESPONSE SENT TO USER" in text
    assert "phase      : confirm" in text
    assert r.json()["rep_id"] in text
