"""Multi-variable comparison resolver and schema routing."""

from __future__ import annotations

from analytics.llm2.schema_inject import build_schema_block
from analytics.models import AnalyticalExecutionPlan
from analytics.resolver.pipeline import resolve_aep
from tests.test_analytics_resolver import (
    _base_catalog,
    _base_entities,
    _entity_variables_ercot,
    _full_variable_catalog,
    _latest_inits,
)


def _houston_temp_load_aep():
    return AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "query": {
                "intent": "forecast",
                "analysis_type": "comparison",
                "entity": {"role": "filter", "mode": "explicit", "values": ["ERCOT"]},
                "location": {
                    "role": "filter",
                    "mode": "explicit",
                    "values": ["Houston"],
                },
                "variable": {
                    "role": "filter",
                    "mode": "explicit",
                    "values": ["temp_2m", "load"],
                },
                "timeframe": {"mode": "relative", "expression": "next_week"},
                "initialization": {"role": "filter", "mode": "latest", "values": []},
                "statistics": {
                    "operation": "percentile",
                    "parameters": {"percentile": 50},
                    "value": 50,
                },
                "comparison": {"enabled": True, "dimensions": ["variable"]},
                "visualization": {
                    "required": True,
                    "chart_type": "line",
                    "x_axis": {"meaning": "hour"},
                    "y_axis": [
                        {"meaning": "2 m Air Temperature P50", "unit": "°C"},
                        {"meaning": "Electric Load P50", "unit": "MW"},
                    ],
                    "legend": "Temperature vs Load — Houston",
                },
            },
        }
    )


def test_multi_variable_resolves_both_variables():
    rep, summary, errors = resolve_aep(
        _houston_temp_load_aep(),
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=_full_variable_catalog(),
        entity_variables=_entity_variables_ercot(),
        current_utc="2026-08-12T08:00:00Z",
    )
    assert errors == []
    assert rep is not None
    assert len(rep.variables) == 2
    names = {v.name for v in rep.variables}
    assert names == {"temp_2m", "load"}
    assert rep.variable.name == "temp_2m"


def test_multi_variable_rep_includes_both_forecast_schemas():
    rep, summary, errors = resolve_aep(
        _houston_temp_load_aep(),
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=_full_variable_catalog(),
        entity_variables=_entity_variables_ercot(),
        current_utc="2026-08-12T08:00:00Z",
    )
    assert errors == []
    assert rep is not None
    assert "weather_forecast" in rep.required_schema
    assert "energy_forecast" in rep.required_schema


def test_multi_variable_rep_serializes_variables_with_location_keys():
    rep, summary, errors = resolve_aep(
        _houston_temp_load_aep(),
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=_full_variable_catalog(),
        entity_variables=_entity_variables_ercot(),
        current_utc="2026-08-12T08:00:00Z",
    )
    assert errors == []
    payload = rep.to_dict()
    assert len(payload["variables"]) == 2
    by_name = {v["name"]: v for v in payload["variables"]}
    assert by_name["temp_2m"]["location_key"] == "weather_sims_id"
    assert by_name["load"]["location_key"] == "energy_sims_id"


def test_multi_variable_confirm_card_copy():
    rep, summary, errors = resolve_aep(
        _houston_temp_load_aep(),
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=_full_variable_catalog(),
        entity_variables=_entity_variables_ercot(),
        current_utc="2026-08-12T08:00:00Z",
    )
    assert errors == []
    assert summary is not None
    assert summary.forecast_representation == "Multi"
    assert "temp_2m" not in summary.computation_summary.lower() or "temperature" in summary.computation_summary.lower()
    assert "load" in summary.computation_summary.lower() or "electric load" in summary.computation_summary.lower()
    assert "weather forecast" in summary.computation_summary.lower()
    assert "energy forecast" in summary.computation_summary.lower()
    assert "2 values per hour" in summary.output_shape


def test_schema_inject_includes_weather_and_energy_for_multi_variable_rep():
    rep, _, errors = resolve_aep(
        _houston_temp_load_aep(),
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=_full_variable_catalog(),
        entity_variables=_entity_variables_ercot(),
        current_utc="2026-08-12T08:00:00Z",
    )
    assert errors == []
    block = build_schema_block(rep.to_dict())
    assert "weather_forecast_ensemble_short" in block
    assert "energy_forecast_ensemble" in block
