"""Tests for session context spec checks."""

from observability.prompt_diff import check_context_against_spec


def test_check_context_complete():
    ctx = {
        "username": "user@example.com",
        "current_utc": "2026-06-21T10:00:00Z",
        "allowed_entities": [
            {
                "entity_id": "uuid",
                "entity": "ERCOT",
                "shortname": "ercot_generic",
                "timezone": "US/Central",
                "is_iso": True,
                "has_forecast": True,
            }
        ],
        "latest_inits": {
            "ercot_generic": {
                "weather": {"forecast": "2026-06-21T08:00:00+00"},
                "energy": {"forecast": "2026-06-21T07:00:00+00"},
                "fundamental_market": {},
            }
        },
        "conversation_state": {
            "entity_shortname": None,
            "location_key": None,
            "variable": None,
            "timeframe": None,
        },
        "variable_units": {"load": "MW", "gsi": "index"},
        "entity_catalog": {},
    }
    assert check_context_against_spec(ctx) == []


def test_check_context_missing_inits():
    ctx = {
        "username": "u",
        "current_utc": "t",
        "allowed_entities": [{"entity_id": "1", "entity": "E", "shortname": "ercot_generic", "timezone": "US/Central", "is_iso": True, "has_forecast": True}],
        "latest_inits": {},
        "conversation_state": {"entity_shortname": None, "location_key": None, "variable": None, "timeframe": None},
        "variable_units": {},
        "entity_catalog": {},
    }
    warnings = check_context_against_spec(ctx)
    assert any("latest_inits" in w for w in warnings)
