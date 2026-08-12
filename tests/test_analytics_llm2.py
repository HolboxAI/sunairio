"""Analytics LLM2 — schema inject, parse, execute (Metadata/Forecast only)."""

from __future__ import annotations

import pytest

from analytics.llm2.executor import (
    AnalyticsExecuteError,
    classify_target,
    execute_plan,
    fill_result_template,
    format_answer_message,
)
from analytics.llm2.parser import Llm2Plan, parse_and_validate
from analytics.llm2.schema_inject import build_schema_block
from analytics.llm2 import run as llm2_run


def test_schema_block_includes_historical_and_blocks_lake():
    rep = {
        "required_schema": ["variables", "locations", "historical_iso_load_gen"],
        "routing": {"historical_database": True, "forecast_database": False},
        "variable": {"name": "load", "category": "Energy"},
    }
    block = build_schema_block(rep)
    assert "historical_iso_load_gen" in block
    assert "NOT ENABLED" in block or "out of scope" in block.lower()


def test_schema_block_expands_weather_forecast():
    rep = {
        "required_schema": ["variables"],
        "routing": {"forecast_database": True},
        "variable": {"name": "temp_2m", "category": "Weather"},
    }
    block = build_schema_block(rep)
    assert "weather_forecast_ensemble_short" in block
    assert "glue." not in block.lower() or "NOT ENABLED" in block


def test_parse_valid_forecast_plan():
    raw = """
    {
      "sql": "SELECT AVG(ensemble_value) AS mean_load FROM energy_forecast_ensemble WHERE project_name = 'pjm_generic'",
      "target": "forecast",
      "assumptions": [],
      "result_template": "Mean load is {mean_load}.",
      "notes": []
    }
    """
    plan, errors = parse_and_validate(raw)
    assert errors == []
    assert plan.target == "forecast"
    assert "energy_forecast_ensemble" in plan.sql


def test_parse_rejects_glue_sql():
    raw = """
    {
      "sql": "SELECT 1 FROM glue.sunairio.energy_forecast_ensemble",
      "target": "forecast",
      "assumptions": [],
      "result_template": null,
      "notes": []
    }
    """
    _plan, errors = parse_and_validate(raw)
    assert any("Lake" in e or "glue" in e.lower() for e in errors)


def test_classify_target():
    assert classify_target(
        "SELECT MAX(hour_value) FROM historical_iso_load_gen WHERE iso = 'PJM'"
    ) == "metadata"
    assert classify_target(
        "SELECT ensemble_value FROM energy_forecast_ensemble WHERE project_name = 'x'"
    ) == "forecast"
    assert classify_target(
        "SELECT * FROM glue.sunairio.energy_forecast_ensemble"
    ) == "lake"
    assert classify_target(
        "SELECT e.ensemble_value FROM energy_forecast_ensemble e "
        "JOIN historical_iso_load_gen h ON true"
    ) == "cross"


def test_execute_plan_metadata(monkeypatch):
    plan = Llm2Plan(
        sql="SELECT MAX(hour_value) AS peak FROM historical_iso_load_gen",
        target="metadata",
    )

    def fake_meta(sql, params=None, request_id=None):
        return {
            "columns": ["peak"],
            "rows": [[100.0]],
            "row_count": 1,
            "truncated": False,
            "backend": "metadata",
            "query_time_ms": 1.0,
        }

    monkeypatch.setattr(
        "analytics.llm2.executor.metadata_db.execute_query", fake_meta
    )
    result, detail = execute_plan(plan, request_id="r1")
    assert result["rows"] == [[100.0]]
    assert detail["backend"] == "metadata"


def test_execute_plan_rejects_lake():
    plan = Llm2Plan(
        sql="SELECT 1 FROM glue.sunairio.energy_forecast_ensemble",
        target="forecast",
    )
    with pytest.raises(AnalyticsExecuteError, match="Lake"):
        execute_plan(plan)


def test_execute_plan_rejects_cross():
    plan = Llm2Plan(
        sql=(
            "SELECT e.ensemble_value FROM energy_forecast_ensemble e, "
            "historical_iso_load_gen h WHERE true"
        ),
        target="forecast",
    )
    with pytest.raises(AnalyticsExecuteError, match="Cross-database"):
        execute_plan(plan)


def test_fill_template_and_format_message():
    result = {"columns": ["peak_mw"], "rows": [[154321.0]], "row_count": 1}
    filled = fill_result_template("Peak was {peak_mw} MW.", result)
    assert filled == "Peak was 154,321 MW."
    plan = Llm2Plan(sql="SELECT 1", target="metadata", notes=[])
    msg = format_answer_message(template_filled=filled, result=result, plan=plan)
    assert "154,321" in msg


def test_run_confirmed_plan_happy_path(monkeypatch):
    from analytics.llm2.parser import Llm2Plan

    plan = Llm2Plan(
        sql="SELECT 42 AS answer",
        target="forecast",
        result_template="Answer is {answer}.",
    )
    monkeypatch.setattr(
        llm2_run.llm2_agent,
        "run_llm2",
        lambda rep, system_prompt=None: (
            plan,
            "{}",
            {"input_tokens": 1, "output_tokens": 2, "model_id": "m", "validation_errors": []},
        ),
    )
    monkeypatch.setattr(
        llm2_run,
        "execute_plan",
        lambda p, request_id=None: (
            {
                "columns": ["answer"],
                "rows": [[42]],
                "row_count": 1,
                "truncated": False,
                "backend": "forecast",
                "query_time_ms": 1,
            },
            {"backend": "forecast"},
        ),
    )
    out = llm2_run.run_confirmed_plan({"intent": "forecast"}, request_id="r")
    assert out["ok"] is True
    assert out["data"]["rows"] == [[42]]
    assert "42" in out["message"]


def test_groupby_scalar_not_eligible_for_shortcut():
    from analytics.historical_scalar import is_eligible
    from analytics.models import (
        ResolvedEntity,
        ResolvedExecutionPlan,
        ResolvedInitialization,
        ResolvedLocations,
        ResolvedTimeframe,
        ResolvedVariable,
    )

    rep = ResolvedExecutionPlan(
        intent="historical",
        analysis_type="scalar",
        entity=ResolvedEntity("1", "pjm_generic", "PJM", "US/Eastern"),
        locations=ResolvedLocations(
            "logical_group",
            1,
            [{"location_name": "PJM", "energy_sims_id": "pjm"}],
            "RTO",
        ),
        variable=ResolvedVariable("load", "Electric Load", "MW", "Energy"),
        timeframe=ResolvedTimeframe("2020-01-01", "2026-08-11"),
        initialization=ResolvedInitialization("none", label="N/A"),
        statistics={"operation": "max", "parameters": {"groupby": "month"}, "value": None},
        routing={"historical_database": True},
        required_schema=["historical_iso_load_gen"],
        visualization={"required": True, "chart": "bar"},
    )
    assert is_eligible(rep) is False
