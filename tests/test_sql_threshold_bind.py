"""Tests for cross-DB SQL rewrite and multi-CTE threshold detection."""

from __future__ import annotations

from analytics.sql_threshold_bind import rewrite_sql_with_bound_threshold
from analytics.threshold_resolve import (
    needs_historical_threshold_resolution,
    resolve_historical_threshold,
)
from security.sql_guard import extract_historical_threshold_cte, is_cross_db_threshold_sql

MULTI_CTE_SQL = (
    "WITH threshold AS ("
    "SELECT MAX(hour_value) AS peak_load_2023 FROM historical_iso_load_gen "
    "WHERE iso = 'PJM' AND region = 'pjm' AND variable = 'load' "
    "AND hour_beginning >= '2023-01-01T00:00:00Z'::timestamptz "
    "AND hour_beginning < '2024-01-01T00:00:00Z'::timestamptz"
    "), "
    "ensemble AS ("
    "SELECT valid_datetime, ensemble_path, ensemble_value "
    "FROM energy_forecast_ensemble "
    "WHERE project_name = 'pjm_generic' AND location = 'pjm' AND variable = 'load'"
    ") "
    "SELECT e.valid_datetime AS hour, "
    "SUM(CASE WHEN e.ensemble_value > t.peak_load_2023 THEN 1 ELSE 0 END) AS paths_above "
    "FROM ensemble e CROSS JOIN threshold t "
    "GROUP BY e.valid_datetime, t.peak_load_2023"
)


def test_is_cross_db_threshold_sql_multi_cte():
    assert is_cross_db_threshold_sql(MULTI_CTE_SQL) is True


def test_extract_historical_threshold_cte_multi_cte():
    parsed = extract_historical_threshold_cte(MULTI_CTE_SQL)
    assert parsed is not None
    cte_name, cte_body, remainder = parsed
    assert cte_name == "threshold"
    assert "historical_iso_load_gen" in cte_body
    assert "energy_forecast_ensemble" in remainder
    assert "CROSS JOIN threshold t" in remainder


def test_rewrite_sql_with_bound_threshold():
    out = rewrite_sql_with_bound_threshold(MULTI_CTE_SQL, 147187.487)
    assert "historical_iso_load_gen" not in out
    assert "CROSS JOIN threshold" not in out
    assert "147187" in out
    assert "energy_forecast_ensemble" in out


def test_nested_threshold_source_object():
    rep = {
        "entity": {"display_name": "PJM", "name": "pjm_generic"},
        "locations": {"values": [{"energy_sims_id": "pjm"}]},
        "variable": {"name": "load"},
        "statistics": {
            "operation": "probability",
            "parameters": {
                "threshold_source": {
                    "intent": "historical",
                    "entity": "PJM",
                    "variable": "load",
                    "timeframe": {"start": "2023-01-01", "end": "2023-12-31"},
                    "aggregation": "max",
                },
                "direction": "above",
            },
        },
    }
    assert needs_historical_threshold_resolution(rep) is True


def test_resolve_nested_threshold_source(monkeypatch):
    rep = {
        "entity": {"display_name": "PJM", "name": "pjm_generic"},
        "locations": {"values": [{"energy_sims_id": "pjm"}]},
        "variable": {"name": "load"},
        "statistics": {
            "operation": "probability",
            "parameters": {
                "threshold_source": {
                    "intent": "historical",
                    "entity": "PJM",
                    "variable": "load",
                    "timeframe": {"start": "2023-01-01", "end": "2023-12-31"},
                    "aggregation": "max",
                },
            },
        },
    }

    monkeypatch.setattr(
        "analytics.threshold_resolve.metadata_db.execute_query",
        lambda sql, params=None, request_id=None: {"rows": [[147187.487]]},
    )
    patched, value = resolve_historical_threshold(rep)
    assert value == 147187.487
    assert patched["statistics"]["parameters"]["threshold"] == 147187.487
