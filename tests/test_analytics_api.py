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
                        {
                            "resource_name": "Coast (Weather Zone)",
                            "energy_sims_id": "coast_wx",
                            "weather_sims_id": "coast_wx",
                            "resource_type": "wx_zone",
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
            "entity_variables": {
                "ercot_generic": {
                    "variables": ["temp_2m", "load"],
                    "weather": ["temp_2m"],
                    # Matches the resource_type used by the resources above
                    "energy_by_resource_type": {"load": ["load", "zone", "portfolio"]},
                    "variables_by_resource_type": {
                        "load": ["load", "temp_2m"],
                        "wx_zone": ["temp_2m"],
                    },
                }
            },
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

    # Default LLM2 stub so confirm tests do not need a live Bedrock client.
    from analytics.llm2 import run as llm2_run

    monkeypatch.setattr(
        llm2_run,
        "run_confirmed_plan",
        lambda rep, request_id=None: {
            "ok": True,
            "message": "Query complete (test stub).",
            "sql": "SELECT 1 AS n",
            "target": "forecast",
            "data": {
                "columns": ["n"],
                "rows": [[1]],
                "row_count": 1,
                "truncated": False,
                "backend": "forecast",
            },
            "result_summary": "Query complete (test stub).",
            "execution": {"backend": "forecast"},
            "llm_usage": {"model_id": "test", "input_tokens": 1, "output_tokens": 1},
            "errors": [],
        },
    )

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

    def fake_run(message, injection, history, system_prompt=None, session_context=None):
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
    # LLM1 assistant_message is the user-facing ask; questions list stays empty
    # so the UI does not append a duplicate questionnaire.
    assert "representation" in (body1["assistant_message"] or "").lower()
    assert body1["questions"] == []

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

    from analytics.llm2 import run as llm2_run

    monkeypatch.setattr(
        llm2_run,
        "run_confirmed_plan",
        lambda rep, request_id=None: {
            "ok": True,
            "message": "Median temperature is ready.",
            "sql": "SELECT 1 AS x",
            "target": "forecast",
            "data": {
                "columns": ["x"],
                "rows": [[1]],
                "row_count": 1,
                "truncated": False,
                "backend": "forecast",
            },
            "result_summary": "Median temperature is ready.",
            "execution": {"backend": "forecast"},
            "llm_usage": {"model_id": "m", "input_tokens": 2, "output_tokens": 3},
            "errors": [],
        },
    )

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
    assert body3["phase"] == "answered"
    assert body3["sql"] == "SELECT 1 AS x"
    assert body3["data"]["row_count"] == 1
    assert "Median temperature" in body3["message"]


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


def test_metadata_locations_answered_without_confirmation(client, monkeypatch):
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
    # A catalog lookup is answered outright: no confirm card, and the locations
    # themselves are in the reply.
    assert body["phase"] == "answered"
    assert not body["rep_id"]
    message = body["assistant_message"] or ""
    assert "Coast (Weather Zone)" in message
    assert "Variable is required" not in message
    # The wx_zone filter must not leak load zones into the answer
    assert "Houston" not in message


def test_metadata_locations_lists_every_type_when_unfiltered(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I'll look up locations for ERCOT.",
            "query": {
                "intent": "metadata",
                "analysis_type": "metadata_lookup",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {"mode": "metadata_query", "values": [], "criteria": {}},
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
        json={"message": "What locations are available in ERCOT?", "session_id": "s_meta2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    message = body["assistant_message"] or ""
    for name in ("Houston", "North", "Coast (Weather Zone)"):
        assert name in message
    assert "Load Zones" in message and "Weather Zones" in message


def test_metadata_variables_ask_is_not_answered_with_locations(client, monkeypatch):
    """LLM1 flags `location` as boilerplate; the wording must pick the catalog."""
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "Here are the variables for ERCOT.",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                # Both flagged, exactly as the model emitted it in production
                "location": {"mode": "metadata_query", "values": [], "criteria": {}},
                "variable": {"mode": "metadata_query", "values": [], "criteria": {}},
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
        json={"message": "which variables are present in ercot", "session_id": "s_vars"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    message = body["assistant_message"] or ""
    assert "temp_2m" in message and "load" in message
    # The locations catalog must not be what comes back
    assert "Houston Load Zone" not in message
    assert "Weather Zones" not in message


def test_metadata_variables_ask_needs_no_location(client, monkeypatch):
    """`location` stays at its default explicit mode; a variables ask has no place."""
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "Here are the variables for ERCOT.",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                # Exactly what the model emits once told to flag only one dimension
                "location": {"mode": "explicit", "values": [], "criteria": {}},
                "variable": {"mode": "metadata_query", "values": [], "criteria": {}},
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
        json={"message": "tell me all the variables in ercot", "session_id": "s_vars2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    message = body["assistant_message"] or ""
    assert "temp_2m" in message
    assert "which location" not in message.lower()


def test_metadata_variables_per_location_is_not_just_locations(client, monkeypatch):
    """'variables per location' must not collapse into a locations-only listing."""
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "Here are the variables available per location type.",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                # Production plan: both dimensions flagged
                "location": {"mode": "metadata_query", "values": [], "criteria": {}},
                "variable": {"mode": "metadata_query", "values": [], "criteria": {}},
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
        json={
            "message": "for ercot tell me name of all variables per location",
            "session_id": "s_per_loc",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    message = body["assistant_message"] or ""
    assert "per location type" in message.lower() or "variables available per" in message.lower()
    assert "temp_2m" in message and "load" in message
    # Must name the places AND the variables — not a bare location list
    assert "Houston" in message or "Coast" in message
    assert "locations you can query" not in message.lower()


def test_metadata_answers_every_entity_the_user_named(client, monkeypatch):
    """"MISO & PJM" must not be answered for MISO alone."""
    from analytics.llm1 import agent as llm1_agent
    from analytics.models import AnalyticalExecutionPlan
    from app.api import routes_analytics

    injection = _injection()
    resolver = injection["_resolver"]
    resolver["allowed_entities"].append(
        {
            "entity_id": "2",
            "entity": "PJM",
            "shortname": "pjm_generic",
            "timezone": "US/Eastern",
        }
    )
    resolver["latest_inits"]["pjm_generic"] = {
        "weather": {"forecast": "2026-08-09T08:00:00+00:00"},
        "energy": {},
        "fundamental_market": {},
    }

    meta = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "Latest initializations for ERCOT and PJM:",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT", "PJM"]},
                "location": {"mode": "explicit", "values": []},
                "variable": {"mode": "explicit", "values": []},
                "timeframe": {"mode": "none"},
                "initialization": {"mode": "metadata_query", "values": []},
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
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: injection)

    r = client.post(
        "/api/v2/consult",
        json={
            "message": "what about ERCOT & PJM, tell me the initializations",
            "session_id": "s_multi",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    message = body["assistant_message"] or ""
    assert "ERCOT" in message and "PJM" in message


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
    assert ok.json()["phase"] == "answered"


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


def test_historical_scalar_is_answered_from_metadata_actuals(client, monkeypatch):
    from analytics import historical_scalar, session_store
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    historical = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I'll pull the 2023 peak.",
            "query": {
                "intent": "historical",
                "analysis_type": "scalar",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {"mode": "explicit", "values": ["Houston"]},
                "variable": {"mode": "explicit", "values": ["load"]},
                "timeframe": {
                    "mode": "explicit",
                    "start": "2023-01-01",
                    "end": "2023-12-31",
                },
                "initialization": {"mode": "none"},
                "statistics": {"operation": "max"},
                "visualization": {"required": False},
            },
            "notes": ["Threshold for tomorrow exceedance probability."],
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
    monkeypatch.setattr(
        historical_scalar.metadata_db,
        "execute_query",
        lambda *a, **k: {"columns": ["scalar_value"], "rows": [[72100.0]]},
    )

    sid = "s_hist_scalar"
    r = client.post(
        "/api/v2/consult",
        json={"message": "What was Houston 2023 peak load?", "session_id": sid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    assert "72,100 MW" in body["assistant_message"]
    assert body.get("rep_id") is None
    assert body["rep_preview"]["routing"]["historical_database"] is True

    refs = session_store.list_references(sid, user_id=1)
    assert refs is not None
    assert len(refs) == 1
    assert refs[0]["value"] == 72100.0
    assert refs[0]["variable"] == "load"


def test_threshold_followup_fetches_actuals_instead_of_awareness_fabrication(
    client, monkeypatch
):
    """Asking what a symbolic pending threshold is must hit Metadata actuals."""
    from analytics import historical_scalar, session_store
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    # Seed a pending forecast plan with a symbolic threshold (the bad confirm card).
    session_store.ensure_tables()
    assert session_store.touch_session("s_thresh_follow", user_id=1)
    session_store.save_pending_rep(
        "s_thresh_follow",
        aep={"status": "resolved", "query": {"intent": "forecast"}},
        rep={
            "intent": "forecast",
            "analysis_type": "probability",
            "entity": {
                "id": "1",
                "name": "ercot_generic",
                "display_name": "ERCOT",
                "timezone": "US/Central",
            },
            "locations": {
                "mode": "explicit",
                "count": 1,
                "values": [
                    {
                        "location_name": "Houston",
                        "energy_sims_id": "houston_cdr",
                        "weather_sims_id": "houston",
                        "resource_type": "load",
                    }
                ],
                "label": "Houston",
            },
            "variable": {
                "name": "load",
                "display_name": "Electric Load",
                "unit": "MW",
                "category": "Energy",
            },
            "timeframe": {"start": "2026-08-11", "end": "2026-08-11", "mode": "relative"},
            "initialization": {"mode": "latest", "resolved": "2026-08-10T23:00:00+00:00"},
            "statistics": {
                "operation": "probability",
                "parameters": {
                    "threshold": "2023_annual_peak_load_mw",
                    "direction": "above",
                },
                "value": None,
            },
            "routing": {"forecast_database": True, "historical_database": False},
            "required_schema": [],
            "visualization": {},
            "comparison": {"enabled": False},
            "notes": [],
        },
        summary={"analysis": "Forecast (probability)"},
    )

    # LLM1 would have fabricated under awareness — backend must override.
    fabricated = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "The 2023 peak was approximately 154,900 MW.",
            "query": {"intent": "awareness"},
            "notes": ["fabricated"],
        }
    )
    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            fabricated,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())
    monkeypatch.setattr(
        historical_scalar.metadata_db,
        "execute_query",
        lambda *a, **k: {"columns": ["scalar_value"], "rows": [[72100.0]]},
    )

    r = client.post(
        "/api/v2/consult",
        json={
            "message": "But whats the 2023_annual_peak_load_mw?",
            "session_id": "s_thresh_follow",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "answered"
    assert "72,100 MW" in body["assistant_message"]
    assert "154,900" not in body["assistant_message"]
    refs = session_store.list_references("s_thresh_follow", user_id=1)
    assert refs and refs[0]["value"] == 72100.0


def test_historical_scalar_falls_back_to_confirm_when_fetch_fails(client, monkeypatch):
    from analytics import historical_scalar
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    historical = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I'll pull the peak.",
            "query": {
                "intent": "historical",
                "analysis_type": "scalar",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {"mode": "explicit", "values": ["Houston"]},
                "variable": {"mode": "explicit", "values": ["load"]},
                "timeframe": {
                    "mode": "explicit",
                    "start": "2023-01-01",
                    "end": "2023-12-31",
                },
                "initialization": {"mode": "none"},
                "statistics": {"operation": "max"},
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
    monkeypatch.setattr(
        historical_scalar.metadata_db,
        "execute_query",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    r = client.post(
        "/api/v2/consult",
        json={"message": "2023 peak?", "session_id": "s_hist_fallback"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "confirm"
    assert body["rep_id"]


def test_consult_writes_one_log_file_per_turn(client, monkeypatch, tmp_path):
    """The turn log must hold the LLM1 prompt, its reply, and what the user sees."""
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    raw = json.dumps(RESOLVED_AEP.to_dict())

    def fake_run(message, injection, history, system_prompt=None, session_context=None):
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

    assert "1. USER REQUEST" in text
    assert "temp next week" in text

    assert "2. LLM1 INPUT REQUEST" in text
    assert "SYSTEM PROMPT SENTINEL" in text
    assert "USER MESSAGE SENTINEL" in text

    assert "3. LLM1 OUTPUT RESPONSE" in text
    assert "temp_2m" in text

    assert "4. RESOLVER INPUT" in text
    assert "5. RESOLVER OUTPUT" in text
    assert "2026-08-10" in text

    assert "6. RESPONSE TO USER (consult)" in text
    assert "phase      : confirm" in text
    assert r.json()["rep_id"] in text


def test_analytics_history_list_hydrate_rename_delete(client, monkeypatch):
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

    sid = "analytics_hist_1"
    r = client.post(
        "/api/v2/consult",
        json={"message": "Temperature forecast for Houston next week", "session_id": sid},
    )
    assert r.status_code == 200
    assert r.json()["phase"] == "confirm"
    rep_id = r.json()["rep_id"]

    listed = client.get("/api/v2/history")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert any(i["session_id"] == sid for i in items)
    match = next(i for i in items if i["session_id"] == sid)
    assert "Temperature forecast" in match["title"]
    assert match["turn_count"] == 1

    # Analytics usage must not pollute classic chat history
    classic = client.get("/api/history")
    assert classic.status_code == 200
    assert not any(i["session_id"] == sid for i in classic.json()["items"])

    hydrated = client.post("/api/v2/history/hydrate", json={"session_id": sid})
    assert hydrated.status_code == 200
    body = hydrated.json()
    assert body["session_id"] == sid
    assert len(body["turns"]) == 2
    assert body["turns"][0]["role"] == "user"
    assert body["turns"][0]["created_at"]
    assert body["pending_rep"]["rep_id"] == rep_id
    assert body["pending_rep"]["summary"]

    renamed = client.patch(
        f"/api/v2/history/sessions/{sid}",
        json={"title": "Houston temp plan"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Houston temp plan"

    deleted = client.delete(f"/api/v2/history/sessions/{sid}")
    assert deleted.status_code == 200
    listed2 = client.get("/api/v2/history")
    assert not any(i["session_id"] == sid for i in listed2.json()["items"])
    missing = client.post("/api/v2/history/hydrate", json={"session_id": sid})
    assert missing.status_code == 404


def test_another_user_cannot_read_analytics_history(client, monkeypatch):
    from analytics.llm1 import agent as llm1_agent
    from app.api import routes_analytics

    monkeypatch.setattr(
        llm1_agent,
        "run_llm1",
        lambda *a, **k: (
            CLARIFY_AEP,
            "{}",
            {"input_tokens": 1, "output_tokens": 1, "model_id": "m"},
        ),
    )
    monkeypatch.setattr(routes_analytics, "build_llm1_injection", lambda user, acl: _injection())

    sid = "analytics_private_hist"
    owner = client.post(
        "/api/v2/consult",
        json={"message": "private analytics chat", "session_id": sid},
    )
    assert owner.status_code == 200

    intruder = _second_user_client(client)
    blocked = client.post(
        "/api/v2/history/hydrate",
        json={"session_id": sid},
        headers=intruder,
    )
    assert blocked.status_code == 404

    listed = client.get("/api/v2/history", headers=intruder)
    assert listed.status_code == 200
    assert not any(i["session_id"] == sid for i in listed.json()["items"])
