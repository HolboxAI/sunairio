"""Location granularity inference and metadata answers."""

from __future__ import annotations

from unittest.mock import patch

from analytics.location_model import infer_composition, infer_granularity
from analytics.metadata_answer import answer_locations
from analytics.models import AnalyticalExecutionPlan


def _aep(criteria=None, values=None):
    return AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "location": {
                    "mode": "metadata_query",
                    "values": values or [],
                    "criteria": criteria or {},
                },
            },
        }
    )


def _catalog():
    return {
        "ercot_generic": {
            "resources": [
                {
                    "resource_name": "Houston Load Zone",
                    "resource_type": "load",
                    "is_aggregate": True,
                },
                {
                    "resource_name": "Coast (Weather Zone)",
                    "resource_type": "wx_zone",
                    "is_aggregate": True,
                },
            ]
        }
    }


def test_bare_locations_ask_is_aggregate():
    assert infer_granularity("What are all the locations in ercot") == "aggregate"
    assert infer_composition("What are all the locations in ercot") is False


def test_stations_ask_is_point():
    assert infer_granularity("What weather stations are in ERCOT?") == "point"


def test_composition_wording():
    assert infer_composition("What stations make up Houston Load Zone?") is True


def test_criteria_overrides_wording():
    assert infer_granularity("stations please", {"granularity": "aggregate"}) == "aggregate"


def test_default_location_list_explains_aggregates():
    text = answer_locations(
        _aep(),
        "ercot_generic",
        "ERCOT",
        _catalog(),
        message="What locations are available in ERCOT?",
        location_types={
            "ercot_generic": {
                "aggregation": {
                    "point_locations": 26,
                    "weighted_parents": 5,
                    "weighted_children": 20,
                }
            }
        },
    )
    assert text is not None
    assert "Houston Load Zone" in text
    assert "aggregate" in text.lower()
    assert "point sites" in text.lower()
    assert "26" in text


def test_wx_filter_does_not_list_load_zones():
    text = answer_locations(
        _aep({"type_filter": ["wx_zone"]}),
        "ercot_generic",
        "ERCOT",
        _catalog(),
        message="What are the weather locations in ercot",
    )
    assert text is not None
    assert "Coast (Weather Zone)" in text
    assert "Houston" not in text


def test_point_list_uses_db_helper():
    points = {
        "ercot_generic": [
            {
                "resource_name": "Abilene",
                "resource_type": "load",
                "is_aggregate": False,
            }
        ]
    }
    with patch(
        "analytics.metadata_answer.metadata_db.load_entity_point_resources",
        return_value=points,
    ):
        text = answer_locations(
            _aep({"granularity": "point"}),
            "ercot_generic",
            "ERCOT",
            _catalog(),
            message="weather stations in ERCOT",
            allowed_entities=[{"shortname": "ercot_generic", "entity_id": "1"}],
        )
    assert text is not None
    assert "Abilene" in text
    assert "Houston Load Zone" not in text
    assert "Load Zones" not in text
    assert "population weather" in text.lower() or "city" in text.lower()


def test_llm2_schema_includes_location_weights():
    from analytics.llm2.schemas import SCHEMA_SLICES, slices_for

    text = "\n".join(slices_for(["locations", "location_weights"]))
    assert "is_aggregate" in text
    assert "parent_location_id" in SCHEMA_SLICES["location_weights"]
    assert "point" in text.lower()
    joins = "\n".join(slices_for(["location_variables", "resource_variables"]))
    assert "location_id" in joins and "resource_id" in joins


def test_composition_lists_weighted_children():
    rows = [
        {
            "parent_name": "West Load Zone",
            "parent_weather_sims_id": "west",
            "child_name": "Abilene",
            "child_weather_sims_id": "9vc",
            "input_variable": "temp_2m",
            "output_variable": "temp_2m",
            "weight": 0.242,
            "is_dynamic": False,
        },
        {
            "parent_name": "West Load Zone",
            "parent_weather_sims_id": "west",
            "child_name": "Lubbock",
            "child_weather_sims_id": "9tz",
            "input_variable": "temp_2m",
            "output_variable": "temp_2m",
            "weight": 0.504,
            "is_dynamic": False,
        },
    ]
    with patch(
        "analytics.metadata_answer.metadata_db.load_location_composition",
        return_value=rows,
    ):
        text = answer_locations(
            _aep({"composition": True}, values=["West Load Zone"]),
            "ercot_generic",
            "ERCOT",
            _catalog(),
            message="what stations make up West Load Zone",
        )
    assert text is not None
    assert "Abilene" in text and "Lubbock" in text
    assert "West Load Zone" in text


def test_independent_weather_is_point():
    assert infer_granularity("How many independent weather locations are there in ercot") == "point"


def test_weather_ones_keeps_prior_point_scope():
    assert (
        infer_granularity(
            "these are both weather and energy perhaps. I only want the weather ones",
            prior_granularity="point",
        )
        == "point"
    )


def test_variables_from_these_uses_prior_list():
    from analytics.metadata_answer import _pick_target, answer_variables_by_location

    aep = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "variable": {"mode": "metadata_query", "values": []},
                "location": {"mode": "metadata_query", "values": [], "criteria": {}},
            },
        }
    )
    prior = {
        "kind": "catalog_location_list",
        "entity": "ercot_generic",
        "granularity": "point",
        "domain": "weather",
        "names": ["Abilene", "Houston"],
    }
    assert _pick_target(aep, "what variables are forecasted from these locations", prior) == (
        "variables_by_location"
    )
    rows = [
        {
            "location_name": "Abilene",
            "variable": "temp_2m",
            "variable_type": "weather",
        },
        {
            "location_name": "Houston",
            "variable": "temp_2m",
            "variable_type": "weather",
        },
        {
            "location_name": "Houston",
            "variable": "dew_2m",
            "variable_type": "weather",
        },
    ]
    with patch(
        "analytics.metadata_answer.metadata_db.load_variables_for_locations",
        return_value=rows,
    ):
        text = answer_variables_by_location(
            aep,
            "ercot_generic",
            "ERCOT",
            _catalog(),
            {},
            [{"variable": "temp_2m", "display_name": "2 m Air Temperature", "unit": "°C"}],
            message="what variables are forecasted from these locations",
            catalog_locations=prior,
        )
    assert text is not None
    assert "not the full ERCOT variable catalog" in text
    assert "temp_2m" in text and "dew_2m" in text
    assert "Abilene" in text and "Houston" in text
    assert "point site" in text


def test_named_zone_gets_weather_and_energy_vars():
    from analytics.metadata_answer import _pick_target, answer_variables_by_location

    aep = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "query": {
                "intent": "metadata",
                "entity": {"mode": "explicit", "values": ["ERCOT"]},
                "variable": {"mode": "metadata_query", "values": []},
                "location": {
                    "mode": "explicit",
                    "values": ["Houston Load Zone"],
                    "criteria": {},
                },
            },
        }
    )
    assert (
        _pick_target(aep, "what variables are forecasted at Houston Load Zone")
        == "variables_by_location"
    )
    rows = [
        {
            "place_name": "Houston Load Zone",
            "location_name": "Houston Load Zone",
            "resource_name": "Houston Load Zone",
            "is_aggregate": True,
            "variable": "temp_2m",
            "variable_type": "weather",
        },
        {
            "place_name": "Houston Load Zone",
            "location_name": "Houston Load Zone",
            "resource_name": "Houston Load Zone",
            "is_aggregate": True,
            "variable": "load",
            "variable_type": "energy",
        },
    ]
    catalog = [
        {"variable": "temp_2m", "display_name": "2 m Air Temperature", "unit": "°C"},
        {"variable": "load", "display_name": "Electric Load", "unit": "MW"},
    ]
    with patch(
        "analytics.metadata_answer.metadata_db.load_variables_for_locations",
        return_value=rows,
    ):
        text = answer_variables_by_location(
            aep,
            "ercot_generic",
            "ERCOT",
            _catalog(),
            {},
            catalog,
            message="what variables are forecasted at Houston Load Zone",
        )
    assert text is not None
    assert "Houston Load Zone" in text
    assert "aggregate zone" in text
    assert "temp_2m" in text and "load" in text
    assert "Weather" in text and "Energy" in text
    assert "not the full ERCOT variable catalog" in text

