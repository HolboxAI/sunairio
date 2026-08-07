"""Deterministic resolver stage tests."""

from analytics.catalog import build_variable_catalog, resolve_variable_name
from analytics.models import AnalyticalExecutionPlan
from analytics.resolver.pipeline import resolve_aep


def _base_entities():
    return [
        {
            "entity_id": "1",
            "entity": "ERCOT",
            "shortname": "ercot_generic",
            "timezone": "US/Central",
        }
    ]


def _base_catalog():
    return {
        "ercot_generic": {
            "portfolio": {
                "energy_sims_id": "ercot_rto",
                "weather_sims_id": "ercot",
            },
            "resources": [
                {
                    "resource_name": "Houston (CDR Zone)",
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
                    "resource_name": "South",
                    "energy_sims_id": "south_raybn",
                    "weather_sims_id": "south",
                    "resource_type": "load",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "West",
                    "energy_sims_id": "west_cdr",
                    "weather_sims_id": "west",
                    "resource_type": "load",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "East",
                    "energy_sims_id": "east_cdr",
                    "weather_sims_id": "east",
                    "resource_type": "load",
                    "is_aggregate": True,
                },
            ],
        }
    }


def _latest_inits():
    return {
        "ercot_generic": {
            "weather": {"forecast": "2026-08-05T18:00:00+00:00"},
            "energy": {"forecast": "2026-08-05T17:00:00+00:00"},
            "fundamental_market": {},
        }
    }


def _resolved_aep(**overrides):
    data = {
        "status": "resolved",
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
    }
    data["query"].update(overrides)
    return AnalyticalExecutionPlan.from_dict(data)


def test_resolve_variable_alias():
    entry = resolve_variable_name("temperature", build_variable_catalog({"temp_2m": "°C"}))
    assert entry is not None
    assert entry["variable"] == "temp_2m"


def test_pipeline_expands_load_zones_and_next_week():
    aep = _resolved_aep()
    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=build_variable_catalog({"temp_2m": "°C", "load": "MW"}),
        current_utc="2026-08-06T12:00:00Z",
    )
    assert errors == []
    assert rep is not None
    assert summary is not None
    assert rep.entity.name == "ercot_generic"
    assert rep.locations.count == 5
    assert rep.locations.mode == "logical_group"
    # Next calendar week after Thu 2026-08-06 US/Central → Mon 2026-08-10 .. Sun 2026-08-16
    assert rep.timeframe.start == "2026-08-10"
    assert rep.timeframe.end == "2026-08-16"
    assert rep.initialization.mode == "latest"
    assert "2026-08-05" in (rep.initialization.resolved or "")
    assert summary.forecast_representation == "Median (P50)"
    assert "weather_forecast" in rep.required_schema


def test_pipeline_rejects_unknown_entity():
    aep = _resolved_aep()
    aep.query.entity.values = ["UNKNOWN_ISO"]
    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=build_variable_catalog({"temp_2m": "°C"}),
        current_utc="2026-08-06T12:00:00Z",
    )
    assert rep is None
    assert any("not in your allowed list" in e for e in errors)


def test_pipeline_explicit_houston():
    aep = _resolved_aep(
        location={"role": "filter", "mode": "explicit", "values": ["Houston"]}
    )
    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=build_variable_catalog({"temp_2m": "°C"}),
        current_utc="2026-08-06T12:00:00Z",
    )
    assert errors == []
    assert rep.locations.count == 1
    assert "Houston" in rep.locations.label
