"""Tests for entity_catalog preload and session context."""

from unittest.mock import MagicMock, patch

from core.models import ConversationState
from core.session_context import build_session_context
from data import metadata_db
from observability.prompt_diff import check_context_against_spec
from security.acl import UserACL


def _reset_catalog_cache():
    metadata_db._entity_catalog_cache = {}
    metadata_db._entity_catalog_ts = 0.0


def test_check_context_complete_with_entity_catalog():
    ctx = {
        "username": "user@example.com",
        "current_utc": "2026-06-21T10:00:00Z",
        "allowed_entities": [
            {
                "entity_id": "uuid",
                "entity": "ERCOT",
                "shortname": "ercot_generic",
                "timezone": "US/Central",
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
        "entity_catalog": {
            "ercot_generic": {
                "portfolio": {"energy_sims_id": "rto", "weather_sims_id": "rto"},
                "resources": [
                    {
                        "resource_name": "Houston (CDR Zone)",
                        "energy_sims_id": "houston_cdr",
                        "weather_sims_id": "houston",
                        "resource_type": "load",
                        "is_aggregate": True,
                    }
                ],
            }
        },
    }
    assert check_context_against_spec(ctx) == []


def test_check_context_missing_entity_catalog():
    ctx = {
        "username": "u",
        "current_utc": "t",
        "allowed_entities": [
            {
                "entity_id": "1",
                "entity": "E",
                "shortname": "ercot_generic",
                "timezone": "US/Central",
            }
        ],
        "latest_inits": {
            "ercot_generic": {"weather": {}, "energy": {}, "fundamental_market": {}}
        },
        "conversation_state": {
            "entity_shortname": None,
            "location_key": None,
            "variable": None,
            "timeframe": None,
        },
        "variable_units": {},
    }
    warnings = check_context_against_spec(ctx)
    assert any("entity_catalog" in w for w in warnings)


def test_load_entity_catalog_builds_portfolio_and_resources():
    _reset_catalog_cache()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.side_effect = [
        [
            (
                "e1",
                "ercot_generic",
                "ERCOT RTO",
                "rto",
                "rto",
                "portfolio",
                True,
            ),
            (
                "e1",
                "ercot_generic",
                "Houston (CDR Zone)",
                "houston_cdr",
                "houston",
                "load",
                True,
            ),
        ],
        [("e1", "ercot_generic")],
    ]

    with patch.object(metadata_db, "get_connection") as mock_get:
        mock_get.return_value.__enter__.return_value = mock_conn
        result = metadata_db.load_entity_catalog(["e1"], force=True)

    assert "ercot_generic" in result
    assert result["ercot_generic"]["portfolio"] == {
        "energy_sims_id": "rto",
        "weather_sims_id": "rto",
    }
    assert len(result["ercot_generic"]["resources"]) == 2
    assert result["ercot_generic"]["resources"][0]["resource_type"] == "portfolio"
    assert result["ercot_generic"]["resources"][1]["energy_sims_id"] == "houston_cdr"
    assert "entity_id" not in result["ercot_generic"]


def test_load_entity_catalog_sql_filters_to_aggregates_and_portfolio():
    _reset_catalog_cache()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.side_effect = [
        [
            (
                "e1",
                "holy_cross_psco",
                "PSCO",
                "psco",
                "psco",
                "zone",
                True,
            ),
        ],
        [("e1", "holy_cross_psco")],
    ]

    with patch.object(metadata_db, "get_connection") as mock_get:
        mock_get.return_value.__enter__.return_value = mock_conn
        result = metadata_db.load_entity_catalog(["e1"], force=True)

    catalog_sql = mock_cur.execute.call_args_list[1].args[0]
    assert "COALESCE(l.is_aggregate, false) = true" in catalog_sql
    assert "rt.resource_type = 'portfolio'" in catalog_sql

    resources = result["holy_cross_psco"]["resources"]
    assert len(resources) == 1
    assert resources[0]["resource_name"] == "PSCO"
    assert resources[0]["is_aggregate"] is True


def test_load_entity_catalog_keeps_non_aggregate_portfolio():
    _reset_catalog_cache()
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.fetchall.side_effect = [
        [
            (
                "e1",
                "ercot_generic",
                "ERCOT RTO",
                "rto",
                "rto",
                "portfolio",
                False,
            ),
        ],
        [("e1", "ercot_generic")],
    ]

    with patch.object(metadata_db, "get_connection") as mock_get:
        mock_get.return_value.__enter__.return_value = mock_conn
        result = metadata_db.load_entity_catalog(["e1"], force=True)

    assert result["ercot_generic"]["portfolio"] == {
        "energy_sims_id": "rto",
        "weather_sims_id": "rto",
    }
    assert len(result["ercot_generic"]["resources"]) == 1
    assert result["ercot_generic"]["resources"][0]["resource_type"] == "portfolio"


def test_load_entity_catalog_cache_hit_skips_db():
    _reset_catalog_cache()
    metadata_db._entity_catalog_cache = {
        "ercot_generic": {
            "entity_id": "e1",
            "portfolio": {"energy_sims_id": "rto", "weather_sims_id": "rto"},
            "resources": [],
        }
    }
    metadata_db._entity_catalog_ts = metadata_db.time.monotonic()

    with patch.object(metadata_db, "get_connection") as mock_get:
        result = metadata_db.load_entity_catalog(["e1"])
        mock_get.assert_not_called()

    assert result == {
        "ercot_generic": {
            "portfolio": {"energy_sims_id": "rto", "weather_sims_id": "rto"},
            "resources": [],
        }
    }


def test_build_session_context_includes_entity_catalog():
    acl = UserACL(username="u", entity_ids=["e1"], project_names=["ercot_generic"])
    catalog = {
        "ercot_generic": {
            "portfolio": {"energy_sims_id": "rto", "weather_sims_id": "rto"},
            "resources": [],
        }
    }
    entities = [
        {
            "entity_id": "e1",
            "entity": "ERCOT",
            "shortname": "ercot_generic",
            "timezone": "US/Central",
        }
    ]

    with (
        patch.object(metadata_db, "load_allowed_entities", return_value=entities),
        patch.object(metadata_db, "get_latest_inits_nested", return_value={}),
        patch.object(metadata_db, "get_variable_units", return_value={"load": "MW"}),
        patch.object(metadata_db, "load_entity_catalog", return_value=catalog) as mock_cat,
    ):
        ctx = build_session_context(
            {"email": "u@example.com"},
            acl,
            ConversationState(),
        )

    mock_cat.assert_called_once_with(["e1"])
    assert ctx.entity_catalog == catalog
    assert ctx.to_dict()["entity_catalog"] == catalog
