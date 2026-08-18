"""Tests for v3 planner parser, placeholders, and DAG executor."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from planner.executor import (
    PlanExecutionError,
    execute_plan,
    is_federated_union_all,
    topological_layers,
)
from planner.models import QueryPlan
from planner.parser import parse_envelope, validate_envelope
from planner.placeholders import (
    UnresolvedPlaceholderError,
    bind_sql,
    extract_contract_values,
)


SIMPLE_PLAN = """{
  "clarity_required": false,
  "clarifying_question": null,
  "question": "P90 GSI for ERCOT tomorrow",
  "understanding": "Hourly P90 GSI for ERCOT RTO tomorrow from latest energy init.",
  "timeframe_rationale": "You asked about tomorrow; that is one local day on the hot energy forecast table.",
  "answer_type": "Sql",
  "assumptions": ["Entity: ercot_generic (ERCOT)", "Location: rto"],
  "suggestions": [],
  "answer": null,
  "query_plan": {
    "steps": [
      {
        "id": "final",
        "purpose": "P90 GSI tomorrow",
        "target": "forecast",
        "sql": "SELECT valid_datetime, percentile_disc(0.90) WITHIN GROUP (ORDER BY ensemble_value) AS p90_gsi FROM energy_forecast_ensemble WHERE project_name = 'ercot_generic'",
        "depends_on": [],
        "returns": {
          "valid_datetime": {"type": "timestamp", "cardinality": "many"},
          "p90_gsi": {"type": "number", "cardinality": "many"}
        }
      }
    ],
    "final_step": "final"
  },
  "final_sql": "SELECT valid_datetime, percentile_disc(0.90) WITHIN GROUP (ORDER BY ensemble_value) AS p90_gsi FROM energy_forecast_ensemble WHERE project_name = 'ercot_generic'",
  "result_template": null,
  "chart_applicable": true,
  "chart_details": {
    "chart_type": "line",
    "x_axis": ["valid_datetime"],
    "y_axis": ["p90_gsi"],
    "x_unit": ["US/Central"],
    "y_unit": ["fraction"]
  }
}"""


TWO_STEP = """{
  "clarity_required": false,
  "clarifying_question": null,
  "question": "Probability load exceeds 2023 peak",
  "understanding": "Lookup 2023 peak then forecast probability.",
  "timeframe_rationale": "Peak is 2023 historical actuals; forecast comparison uses the current energy forecast window on one hot table.",
  "answer_type": "Sql",
  "assumptions": ["Entity: pjm_generic (PJM)"],
  "answer": null,
  "query_plan": {
    "steps": [
      {
        "id": "historical_peak",
        "purpose": "Find the 2023 maximum PJM load",
        "target": "metadata",
        "sql": "SELECT MAX(hour_value) AS peak_mw FROM historical_iso_load_gen WHERE iso = 'PJM'",
        "depends_on": [],
        "returns": {"peak_mw": {"type": "number", "cardinality": "one"}}
      },
      {
        "id": "final",
        "purpose": "Forecast probability above peak",
        "target": "forecast",
        "sql": "SELECT COUNT(*)::float / 1000.0 AS probability FROM energy_forecast_ensemble WHERE ensemble_value > {{historical_peak.peak_mw}}",
        "depends_on": ["historical_peak"],
        "returns": {"probability": {"type": "number", "cardinality": "one"}}
      }
    ],
    "final_step": "final"
  },
  "final_sql": "SELECT COUNT(*)::float / 1000.0 AS probability FROM energy_forecast_ensemble WHERE ensemble_value > {{historical_peak.peak_mw}}",
  "result_template": "The probability is {probability}.",
  "chart_applicable": false,
  "chart_details": null
}"""


def test_parse_simple_plan():
    env = parse_envelope(SIMPLE_PLAN)
    assert env.answer_type == "Sql"
    assert env.query_plan.final_step == "final"
    assert validate_envelope(env) == []


def test_parse_legacy_assumption_key():
    raw = json.loads(SIMPLE_PLAN)
    raw["assumption"] = raw.pop("assumptions")
    env = parse_envelope(json.dumps(raw))
    assert env.assumptions
    assert validate_envelope(env) == []


def test_sql_requires_timeframe_rationale():
    raw = json.loads(SIMPLE_PLAN)
    raw["timeframe_rationale"] = None
    env = parse_envelope(json.dumps(raw))
    errors = validate_envelope(env)
    assert any("timeframe_rationale" in e for e in errors)


def test_two_step_placeholder_requires_depends_on():
    env = parse_envelope(TWO_STEP)
    assert validate_envelope(env) == []
    broken = json.loads(TWO_STEP)
    broken["query_plan"]["steps"][1]["depends_on"] = []
    env2 = parse_envelope(json.dumps(broken))
    errors = validate_envelope(env2)
    assert any("depends_on" in e for e in errors)


def test_bind_sql_literal_number():
    sql, params = bind_sql(
        "SELECT 1 WHERE x > {{historical_peak.peak_mw}}",
        {"historical_peak": {"peak_mw": 84231}},
        parameterized=False,
    )
    assert "84231" in sql
    assert "{{" not in sql
    assert params == ()


def test_bind_sql_parameterized():
    sql, params = bind_sql(
        "SELECT 1 WHERE x > {{historical_peak.peak_mw}}",
        {"historical_peak": {"peak_mw": 84231.5}},
        parameterized=True,
    )
    assert "%s" in sql
    assert params == (84231.5,)


def test_bind_sql_escapes_quotes():
    sql, _ = bind_sql(
        "SELECT 1 WHERE loc = {{s.location}}",
        {"s": {"location": "O'Hare"}},
        parameterized=False,
    )
    assert "O''Hare" in sql


def test_unresolved_placeholder():
    with pytest.raises(UnresolvedPlaceholderError):
        bind_sql("SELECT {{missing.col}}", {})


def test_extract_contract_one_row():
    got = extract_contract_values(
        ["peak_mw"],
        [[84231]],
        {"peak_mw": {"type": "number", "cardinality": "one"}},
    )
    assert got["peak_mw"] == 84231


def test_extract_rejects_null():
    with pytest.raises(ValueError, match="NULL"):
        extract_contract_values(
            ["peak_mw"],
            [[None]],
            {"peak_mw": {"type": "number", "cardinality": "one"}},
        )


def test_extract_rejects_extra_columns():
    with pytest.raises(ValueError, match="Unexpected"):
        extract_contract_values(
            ["peak_mw", "extra"],
            [[1, 2]],
            {"peak_mw": {"type": "number", "cardinality": "one"}},
        )


def test_extract_rejects_many_rows_for_one():
    with pytest.raises(ValueError, match="cardinality one"):
        extract_contract_values(
            ["peak_mw"],
            [[1], [2]],
            {"peak_mw": {"type": "number", "cardinality": "one"}},
        )


def test_topological_layers_parallel():
    plan = QueryPlan.from_dict(
        {
            "final_step": "final",
            "steps": [
                {
                    "id": "a",
                    "purpose": "a",
                    "target": "metadata",
                    "sql": "SELECT 1 AS x",
                    "depends_on": [],
                    "returns": {"x": {"type": "number", "cardinality": "one"}},
                },
                {
                    "id": "b",
                    "purpose": "b",
                    "target": "metadata",
                    "sql": "SELECT 1 AS y",
                    "depends_on": [],
                    "returns": {"y": {"type": "number", "cardinality": "one"}},
                },
                {
                    "id": "final",
                    "purpose": "f",
                    "target": "forecast",
                    "sql": "SELECT {{a.x}} + {{b.y}} AS z",
                    "depends_on": ["a", "b"],
                    "returns": {"z": {"type": "number", "cardinality": "one"}},
                },
            ],
        }
    )
    layers = topological_layers(plan)
    assert len(layers[0]) == 2
    assert layers[1][0].id == "final"


def test_execute_plan_skip_final_binds_placeholder():
    env = parse_envelope(TWO_STEP)

    def fake_execute(sql, target, request_id, params):
        if target == "metadata":
            return {"columns": ["peak_mw"], "rows": [[84231]], "row_count": 1}
        raise AssertionError("final should be skipped")

    plan, result, values = execute_plan(
        env.query_plan, skip_final=True, execute_fn=fake_execute
    )
    assert result is None
    assert values["historical_peak"]["peak_mw"] == 84231
    final = plan.step_map()["final"]
    assert "84231" in (final.bound_sql or "")


def test_federated_union_all_keeps_forecast_target():
    sql = (
        "SELECT DATE(valid_datetime) AS day, AVG(ensemble_value) AS p50_gsi "
        "FROM energy_forecast_ensemble WHERE valid_datetime <= initialization + interval '14 days' "
        "GROUP BY 1 "
        "UNION ALL "
        "SELECT CAST(valid_datetime AS DATE) AS day, AVG(ensemble_value) AS p50_gsi "
        "FROM glue.sunairio.energy_base_ensemble "
        "WHERE valid_datetime > TIMESTAMPADD(HOUR, 336, initialization) GROUP BY 1"
    )
    assert is_federated_union_all(sql) is True

    env = parse_envelope(SIMPLE_PLAN)
    env.query_plan.steps[0].sql = sql
    env.final_sql = sql
    seen = []

    def fake_execute(exec_sql, target, request_id, params):
        seen.append(target)
        return {
            "columns": ["valid_datetime", "p90_gsi"],
            "rows": [["2026-01-01", 0.5]],
            "row_count": 1,
        }

    _plan, result, _ = execute_plan(env.query_plan, execute_fn=fake_execute)
    assert seen == ["forecast"]
    assert result["row_count"] == 1

    wrapped = (
        "SELECT day, AVG(p50_gsi) AS avg_gsi FROM ("
        "SELECT DATE(valid_datetime) AS day, AVG(ensemble_value) AS p50_gsi "
        "FROM energy_forecast_ensemble GROUP BY 1 "
        "UNION ALL "
        "SELECT CAST(valid_datetime AS DATE) AS day, AVG(ensemble_value) AS p50_gsi "
        "FROM glue.sunairio.energy_base_ensemble GROUP BY 1"
        ") combined GROUP BY day"
    )
    assert is_federated_union_all(wrapped) is True


def test_execute_plan_full():
    env = parse_envelope(TWO_STEP)

    def fake_execute(sql, target, request_id, params):
        if "historical_iso" in sql.lower() or target == "metadata":
            return {"columns": ["peak_mw"], "rows": [[84231.0]], "row_count": 1}
        return {"columns": ["probability"], "rows": [[0.12]], "row_count": 1}

    plan, result, _values = execute_plan(env.query_plan, execute_fn=fake_execute)
    assert result["rows"][0][0] == 0.12
    assert "84231" in (plan.step_map()["final"].bound_sql or "")


def test_mid_list_distinct_is_rejected():
    env = parse_envelope(SIMPLE_PLAN)
    env.query_plan.steps[0].sql = (
        "SELECT 'wind_gen' AS variable, DISTINCT location FROM energy_forecast_ensemble"
    )
    env.final_sql = env.query_plan.steps[0].sql
    errors = validate_envelope(env)
    assert any("DISTINCT after a SELECT-list comma" in e for e in errors)


def test_count_distinct_is_allowed():
    env = parse_envelope(SIMPLE_PLAN)
    env.query_plan.steps[0].sql = (
        "SELECT COUNT(DISTINCT location) AS location_count FROM energy_forecast_ensemble "
        "WHERE ensemble_path = 1"
    )
    env.final_sql = env.query_plan.steps[0].sql
    errors = validate_envelope(env)
    assert not any("DISTINCT" in e for e in errors)


def test_cycle_rejected():
    plan = QueryPlan.from_dict(
        {
            "final_step": "a",
            "steps": [
                {
                    "id": "a",
                    "purpose": "a",
                    "target": "metadata",
                    "sql": "SELECT 1 AS x",
                    "depends_on": ["b"],
                    "returns": {"x": {"type": "number", "cardinality": "one"}},
                },
                {
                    "id": "b",
                    "purpose": "b",
                    "target": "metadata",
                    "sql": "SELECT 1 AS y",
                    "depends_on": ["a"],
                    "returns": {"y": {"type": "number", "cardinality": "one"}},
                },
            ],
        }
    )
    with pytest.raises(PlanExecutionError, match="cycle"):
        topological_layers(plan)


def test_clarify_envelope():
    raw = """{
      "clarity_required": true,
      "clarifying_question": ["Which project?"],
      "question": "GSI probability",
      "understanding": null,
      "answer_type": "Sql",
      "assumptions": [],
      "answer": null,
      "query_plan": null,
      "final_sql": null
    }"""
    env = parse_envelope(raw)
    assert validate_envelope(env) == []


def test_planner_routes_registered():
    from app.main import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert "/planner" in paths
    assert "/chat" in paths
    assert "/analytics" in paths
    assert any(str(p).startswith("/api/v3") for p in paths)
