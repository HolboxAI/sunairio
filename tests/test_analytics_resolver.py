"""Deterministic resolver stage tests."""

import pytest

from analytics.catalog import (
    build_variable_catalog,
    groups_for_resource_types,
    public_variable_catalog,
    resolve_variable_name,
)
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
                    "resource_name": "Houston Load Zone",
                    "energy_sims_id": "houston",
                    "weather_sims_id": "houston",
                    "resource_type": "zone",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "North Load Zone",
                    "energy_sims_id": "north",
                    "weather_sims_id": "north",
                    "resource_type": "zone",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "South Load Zone",
                    "energy_sims_id": "south",
                    "weather_sims_id": "south",
                    "resource_type": "zone",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "West Load Zone",
                    "energy_sims_id": "west",
                    "weather_sims_id": "west",
                    "resource_type": "zone",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "Houston (CDR Zone)",
                    "energy_sims_id": "houston_cdr",
                    "weather_sims_id": "houston",
                    "resource_type": "cdr_zone",
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


def _full_variable_catalog():
    return build_variable_catalog(
        {
            "temp_2m": "°C",
            "temp_100m": "°C",
            "wind_speed_100m": "m/s",
            "wind_speed_10m": "m/s",
            "wind_gen": "MW",
            "solar_radiation": "W/m2",
            "solar_gen": "MW",
            "load": "MW",
            "gsi": "Index",
        }
    )


def test_resolve_variable_alias():
    entry = resolve_variable_name("temperature", build_variable_catalog({"temp_2m": "°C"}))
    assert entry is not None
    assert entry["variable"] == "temp_2m"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("wind generation", "wind_gen"),
        ("wind power", "wind_gen"),
        ("solar generation", "solar_gen"),
        ("solar power", "solar_gen"),
        ("pv generation", "solar_gen"),
        ("wind speed", "wind_speed_100m"),
        ("10m wind", "wind_speed_10m"),
        ("100m temperature", "temp_100m"),
        ("temperature", "temp_2m"),
        ("demand", "load"),
        ("stress index", "gsi"),
        ("irradiance", "solar_radiation"),
    ],
)
def test_generation_phrases_beat_shorter_weather_aliases(phrase, expected):
    """A generic alias like 'wind' must never outrank a precise one like 'wind generation'."""
    entry = resolve_variable_name(phrase, _full_variable_catalog())
    assert entry is not None, phrase
    assert entry["variable"] == expected


def test_unknown_variable_returns_none():
    assert resolve_variable_name("barometric pressure", _full_variable_catalog()) is None


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
    assert rep.locations.count == 4
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
    assert any("couldn't match" in e.lower() or "allowed" in e.lower() for e in errors)


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


def test_metadata_weather_locations_skips_variable():
    aep = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I'll look up weather locations for ERCOT.",
            "query": {
                "intent": "metadata",
                "analysis_type": "metadata_lookup",
                "entity": {"role": "filter", "mode": "explicit", "values": ["ERCOT"]},
                "location": {
                    "role": "dimension",
                    "mode": "metadata_query",
                    "values": [],
                    "criteria": {"type_filter": ["wx_zone"]},
                },
                "variable": {"role": "filter", "mode": "explicit", "values": []},
                "timeframe": {"mode": "none"},
                "initialization": {"role": "filter", "mode": "none", "values": []},
                "statistics": {},
                "visualization": {"required": False},
            },
        }
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
    assert rep is not None
    assert summary is not None
    assert summary.locations == "Weather locations"
    assert "catalog" in summary.forecast_representation.lower() or "metadata" in summary.forecast_representation.lower() or summary.forecast_representation == "Catalog lookup"


def test_awareness_does_not_require_entity_in_resolver():
    """Awareness is short-circuited in the API; resolver should also be soft."""
    aep = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "assistant_message": "I can help with forecasts and metadata.",
            "query": {
                "intent": "awareness",
                "entity": {"values": []},
                "variable": {"values": []},
                "location": {"values": []},
            },
        }
    )
    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=_base_entities(),
        latest_inits=_latest_inits(),
        entity_catalog=_base_catalog(),
        variable_catalog=build_variable_catalog({"temp_2m": "°C"}),
        current_utc="2026-08-06T12:00:00Z",
    )
    # Soft placeholders allow a metadata-like confirmation; API skips this path for awareness
    assert errors == [] or not any("Entity is required" in e for e in errors)


def _run(aep, *, entities=None, catalog=None, inits=None, now="2026-08-06T12:00:00Z"):
    return resolve_aep(
        aep,
        allowed_entities=entities if entities is not None else _base_entities(),
        latest_inits=inits if inits is not None else _latest_inits(),
        entity_catalog=catalog if catalog is not None else _base_catalog(),
        variable_catalog=_full_variable_catalog(),
        current_utc=now,
    )


def _historical_aep(**overrides):
    query = {
        "intent": "historical",
        "analysis_type": "time_series",
        "entity": {"mode": "explicit", "values": ["ERCOT"]},
        "location": {"mode": "explicit", "values": ["Houston"]},
        "variable": {"mode": "explicit", "values": ["load"]},
        "timeframe": {"mode": "explicit", "start": "2026-07-01", "end": "2026-07-31"},
        "initialization": {"mode": "latest"},
        "statistics": {"operation": "mean"},
        "visualization": {"required": True, "chart_type": "line"},
    }
    query.update(overrides)
    return AnalyticalExecutionPlan.from_dict({"status": "resolved", "query": query})


def test_historical_has_no_forecast_initialization():
    rep, summary, errors = _run(_historical_aep())
    assert errors == []
    assert rep.initialization.mode == "none"
    assert rep.initialization.resolved is None
    assert "Latest Forecast" not in summary.initialization
    assert rep.routing["historical_database"] is True
    assert rep.routing["forecast_database"] is False


def test_historical_without_initialization_is_not_blocked():
    """An omitted initialization arrives as explicit-with-no-values; it must not error."""
    rep, summary, errors = _run(_historical_aep(initialization={}))
    assert errors == []
    assert rep.initialization.mode == "none"


@pytest.mark.parametrize(
    "expression,start,end",
    [
        ("last_week", "2026-07-27", "2026-08-02"),
        ("yesterday", "2026-08-05", "2026-08-05"),
        ("last_7_days", "2026-07-31", "2026-08-06"),
        ("past_30_days", "2026-07-08", "2026-08-06"),
        ("last_month", "2026-07-01", "2026-07-31"),
        ("year_to_date", "2026-01-01", "2026-08-06"),
    ],
)
def test_past_relative_timeframes_resolve(expression, start, end):
    rep, _summary, errors = _run(
        _historical_aep(timeframe={"mode": "relative", "expression": expression})
    )
    assert errors == []
    assert (rep.timeframe.start, rep.timeframe.end) == (start, end)


def test_historical_range_in_the_future_is_questioned():
    rep, _summary, errors = _run(
        _historical_aep(
            timeframe={"mode": "explicit", "start": "2026-09-01", "end": "2026-09-30"}
        )
    )
    assert rep is None
    assert any("future" in e.lower() for e in errors)


def test_inverted_explicit_range_is_questioned():
    rep, _summary, errors = _run(
        _historical_aep(
            timeframe={"mode": "explicit", "start": "2026-07-31", "end": "2026-07-01"}
        )
    )
    assert rep is None
    assert any("starts after it ends" in e for e in errors)


def test_ambiguous_entity_asks_instead_of_guessing():
    entities = _base_entities() + [
        {
            "entity_id": "2",
            "entity": "ISONE",
            "shortname": "isone_generic",
            "timezone": "US/Eastern",
        }
    ]
    aep = _resolved_aep()
    aep.query.entity.values = ["ISO"]
    rep, _summary, errors = _run(aep, entities=entities)
    assert rep is None
    # "ISO" is a substring of ISONE but not a whole-token match, so it resolves to neither
    assert any("couldn't match" in e for e in errors)


def test_ambiguous_location_asks_instead_of_guessing():
    catalog = {
        "ercot_generic": {
            "portfolio": None,
            "resources": [
                {
                    "resource_name": "Houston North Load Zone",
                    "energy_sims_id": "houston_north",
                    "weather_sims_id": "houston_north",
                    "resource_type": "zone",
                },
                {
                    "resource_name": "Houston South Load Zone",
                    "energy_sims_id": "houston_south",
                    "weather_sims_id": "houston_south",
                    "resource_type": "zone",
                },
            ],
        }
    }
    aep = _resolved_aep(location={"mode": "explicit", "values": ["Houston"]})
    rep, _summary, errors = _run(aep, catalog=catalog)
    assert rep is None
    assert any("Which one did you mean" in e for e in errors)


def test_explicit_location_prefers_load_zone_over_cdr_zone():
    aep = _resolved_aep(location={"mode": "explicit", "values": ["Houston"]})
    rep, _summary, errors = _run(aep)
    assert errors == []
    assert rep.locations.values[0]["resource_type"] == "zone"


def test_all_gaps_are_reported_in_one_turn():
    """Independent gaps should surface together rather than one round trip each."""
    aep = _resolved_aep(
        variable={"mode": "explicit", "values": []},
        timeframe={"mode": "relative", "expression": "sometime soon"},
    )
    rep, _summary, errors = _run(aep)
    assert rep is None
    assert any("variable" in e.lower() for e in errors)
    assert any("timeframe" in e.lower() for e in errors)


def test_missing_entity_does_not_also_ask_about_locations():
    aep = _resolved_aep()
    aep.query.entity.values = []
    rep, _summary, errors = _run(aep)
    assert rep is None
    assert any("Which entity" in e for e in errors)
    assert not any("before I can resolve locations" in e for e in errors)


def _group_names(type_counts):
    return [g["name"] for g in groups_for_resource_types(type_counts)]


def test_load_zone_only_entity_is_not_offered_wind_or_solar_groups():
    """ISONE/MISO carry only load zones; advertising other groups would be a lie."""
    names = _group_names({"portfolio": 1, "zone": 8})
    assert names == ["RTO", "All Load Zones"]


def test_entity_with_every_resource_type_keeps_every_group():
    names = _group_names(
        {"portfolio": 1, "zone": 4, "solar_zone": 6, "wind_zone": 5, "wx_zone": 8}
    )
    assert names == [
        "RTO",
        "All Load Zones",
        "All Solar Zones",
        "All Wind Zones",
        "All Weather Zones",
    ]


def test_rto_survives_even_without_a_portfolio_resource_row():
    """The location stage synthesises RTO from the entity catalog, so keep offering it."""
    assert _group_names({"zone": 3}) == ["RTO", "All Load Zones"]
    assert _group_names({}) == ["RTO"]


def test_zero_count_resource_type_is_not_treated_as_available():
    assert "All Wind Zones" not in _group_names({"zone": 4, "wind_zone": 0})


def test_llm1_variable_view_carries_no_alias_table():
    """Synonym matching is LLM1's job; aliases are resolver-side machinery."""
    public = public_variable_catalog(_full_variable_catalog())
    assert public, "expected a non-empty catalog"
    for entry in public:
        assert "aliases" not in entry
        assert set(entry) == {"variable", "display_name", "category", "unit"}


def test_llm1_variable_view_keeps_every_variable_and_its_unit():
    full = _full_variable_catalog()
    public = public_variable_catalog(full)
    assert [e["variable"] for e in public] == [e["variable"] for e in full]
    assert [e["unit"] for e in public] == [e["unit"] for e in full]


def test_resolver_still_matches_aliases_from_its_own_catalog():
    """Stripping aliases for LLM1 must not weaken the deterministic safety net."""
    entry = resolve_variable_name("demand", _full_variable_catalog())
    assert entry is not None
    assert entry["variable"] == "load"
