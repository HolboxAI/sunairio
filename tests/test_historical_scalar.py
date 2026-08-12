"""Historical scalar reference answers from Metadata DB actuals."""

from __future__ import annotations

from analytics.historical_scalar import (
    format_answer,
    is_eligible,
    result_to_ref,
    try_answer,
)
from analytics.models import (
    ResolvedEntity,
    ResolvedExecutionPlan,
    ResolvedInitialization,
    ResolvedLocations,
    ResolvedTimeframe,
    ResolvedVariable,
)


def _rep(**overrides) -> ResolvedExecutionPlan:
    base = dict(
        intent="historical",
        analysis_type="scalar",
        entity=ResolvedEntity(
            id="1",
            name="pjm_generic",
            display_name="PJM",
            timezone="US/Eastern",
        ),
        locations=ResolvedLocations(
            mode="logical_group",
            count=1,
            values=[
                {
                    "location_name": "PJM",
                    "weather_sims_id": "pjm",
                    "energy_sims_id": "pjm",
                    "resource_type": "portfolio",
                }
            ],
            label="RTO",
        ),
        variable=ResolvedVariable(
            name="load",
            display_name="Electric Load",
            unit="MW",
            category="Energy",
        ),
        timeframe=ResolvedTimeframe(
            start="2023-01-01",
            end="2023-12-31",
            mode="explicit",
        ),
        initialization=ResolvedInitialization(mode="none", label="N/A"),
        statistics={"operation": "max", "parameters": {}, "value": None},
        routing={
            "forecast_database": False,
            "historical_database": True,
            "forecast_evolution": False,
            "metadata": False,
        },
        required_schema=["variables", "locations", "historical_iso_load_gen"],
        visualization={"required": False, "chart": None, "x": "Time", "y": "", "legend": None, "unit": "MW"},
        comparison={"enabled": False, "dimensions": []},
        notes=[],
    )
    base.update(overrides)
    return ResolvedExecutionPlan(**base)


def test_eligible_historical_scalar_max():
    assert is_eligible(_rep()) is True


def test_time_series_not_eligible():
    assert is_eligible(_rep(analysis_type="time_series")) is False


def test_forecast_not_eligible():
    assert is_eligible(_rep(intent="forecast", analysis_type="time_series")) is False


def test_weather_historical_not_eligible():
    assert (
        is_eligible(
            _rep(
                variable=ResolvedVariable(
                    name="temp_2m",
                    display_name="Temperature",
                    unit="°C",
                    category="Weather",
                ),
                required_schema=["variables", "locations", "historical_weather"],
            )
        )
        is False
    )


def test_multi_location_not_eligible():
    locs = ResolvedLocations(
        mode="explicit",
        count=2,
        values=[
            {"location_name": "A", "energy_sims_id": "a"},
            {"location_name": "B", "energy_sims_id": "b"},
        ],
        label="2 locations",
    )
    assert is_eligible(_rep(locations=locs)) is False


def test_try_answer_formats_and_builds_ref(monkeypatch):
    from analytics import historical_scalar

    def fake_execute(sql, params=None, request_id=None):
        assert "MAX(hour_value)" in sql
        assert params["iso"] == "PJM"
        assert params["region"] == "pjm"
        assert params["variable"] == "load"
        assert params["start"] == "2023-01-01"
        assert params["end"] == "2023-12-31"
        return {"columns": ["scalar_value"], "rows": [[154321.0]]}

    monkeypatch.setattr(historical_scalar.metadata_db, "execute_query", fake_execute)

    out = try_answer(_rep())
    assert out is not None
    text, result = out
    assert "154,321 MW" in text
    assert "PJM" in text
    assert "RTO" in text
    assert "2023" in text
    ref = result_to_ref(result)
    assert ref["value"] == 154321.0
    assert ref["kind"] == "historical_scalar"
    assert "pjm" in ref["key"]


def test_try_answer_falls_back_on_db_error(monkeypatch):
    from analytics import historical_scalar

    def boom(*a, **k):
        raise RuntimeError("metadata down")

    monkeypatch.setattr(historical_scalar.metadata_db, "execute_query", boom)
    assert try_answer(_rep()) is None


def test_try_answer_falls_back_when_null(monkeypatch):
    from analytics import historical_scalar

    monkeypatch.setattr(
        historical_scalar.metadata_db,
        "execute_query",
        lambda *a, **k: {"columns": ["scalar_value"], "rows": [[None]]},
    )
    assert try_answer(_rep()) is None


def test_format_answer_mean():
    from analytics.historical_scalar import HistoricalScalarResult

    text = format_answer(
        HistoricalScalarResult(
            value=1234.5,
            operation="mean",
            sql_agg="AVG",
            iso="ERCOT",
            region="houston_cdr",
            variable="load",
            unit="MW",
            start="2023-07-01",
            end="2023-07-07",
            location_label="Houston",
            entity_label="ERCOT",
            variable_label="Electric Load",
            ref_key="k",
        )
    )
    assert "average" in text
    assert "1,234.50 MW" in text
    assert "2023-07-01 → 2023-07-07" in text


def test_threshold_value_question_detection():
    from analytics.historical_scalar import looks_like_threshold_value_question

    assert looks_like_threshold_value_question("But whats the 2023_annual_peak_load_mw?")
    assert looks_like_threshold_value_question("how did you calculated this value?")
    assert not looks_like_threshold_value_question("hour by hour")


def test_rep_from_pending_symbolic_threshold():
    from analytics.historical_scalar import historical_scalar_rep_from_pending

    pending_rep = _rep().to_dict()
    pending_rep["intent"] = "forecast"
    pending_rep["analysis_type"] = "probability"
    pending_rep["statistics"] = {
        "operation": "probability",
        "parameters": {"threshold": "2023_annual_peak_load_mw", "direction": "above"},
        "value": None,
    }
    hist = historical_scalar_rep_from_pending(pending_rep)
    assert hist is not None
    assert hist.intent == "historical"
    assert hist.analysis_type == "scalar"
    assert hist.statistics["operation"] == "max"
    assert hist.timeframe.start == "2023-01-01"
    assert hist.timeframe.end == "2023-12-31"


def test_try_answer_threshold_followup_overrides_fabrication(monkeypatch):
    from analytics import historical_scalar

    monkeypatch.setattr(
        historical_scalar.metadata_db,
        "execute_query",
        lambda *a, **k: {"columns": ["scalar_value"], "rows": [[148200.0]]},
    )
    pending = {
        "rep": {
            **_rep().to_dict(),
            "intent": "forecast",
            "analysis_type": "probability",
            "statistics": {
                "operation": "probability",
                "parameters": {
                    "threshold": "2023_annual_peak_load_mw",
                    "direction": "above",
                },
            },
        }
    }
    out = historical_scalar.try_answer_threshold_followup(
        "But whats the 2023_annual_peak_load_mw?", pending
    )
    assert out is not None
    text, result, hist_rep = out
    assert "148,200 MW" in text
    assert result.value == 148200.0
    assert hist_rep.intent == "historical"
