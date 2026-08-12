"""Workstream 3 — multi-row session threshold references."""

from __future__ import annotations

from analytics.models import AnalyticalExecutionPlan, AnalyticalQuery, StatisticsSpec
from analytics.result_refs import (
    apply_session_thresholds,
    extract_from_result,
    infer_period_from_timeframe,
    latest_location_threshold_table,
)


def test_extract_from_result_daily_totals():
    payload = {
        "columns": ["location_name", "peak_daily_total_mwh", "peak_date"],
        "rows": [
            ["RTO", 95000.5, "2023-07-19"],
            ["MIDATL", 42000.0, "2023-07-19"],
        ],
    }
    ref = extract_from_result(payload, entity="PJM", period="2023")
    assert ref is not None
    assert ref["kind"] == "location_threshold_table"
    assert ref["metric"] == "daily_total_mwh"
    assert len(ref["rows"]) == 2
    assert ref["rows"][0]["value"] == 95000.5


def test_apply_session_thresholds_patches_aep():
    refs = [
        {
            "key": "pjm_2023",
            "kind": "location_threshold_table",
            "metric": "daily_total_mwh",
            "rows": [
                {"location_id": "rto", "location_name": "RTO", "value": 1000.0},
                {"location_id": "midatl", "location_name": "MIDATL", "value": 500.0},
            ],
        }
    ]
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            statistics=StatisticsSpec(operation="probability", parameters={}),
        ),
    )
    patched = apply_session_thresholds(
        aep,
        refs,
        "Probability each location exceeds its respective threshold tomorrow",
    )
    params = patched.query.statistics.parameters
    assert params["threshold_mode"] == "per_location"
    assert params["thresholds"]["rto"] == 1000.0
    assert params["aggregation"] == "daily_sum"


def test_latest_location_threshold_table():
    refs = [
        {"kind": "historical_scalar", "key": "x", "value": 1},
        {
            "kind": "location_threshold_table",
            "key": "tbl",
            "rows": [{"location_id": "a", "value": 1.0}],
        },
    ]
    table = latest_location_threshold_table(refs)
    assert table is not None
    assert table["key"] == "tbl"


def test_infer_period_from_timeframe():
    assert infer_period_from_timeframe("2023-01-01", "2023-12-31") == "2023"
    assert infer_period_from_timeframe("2022-01-01", "2023-12-31") == "2022_2023"
