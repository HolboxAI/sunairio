"""Workstream 2 — proactive ambiguity detection and slot resolution."""

from __future__ import annotations

from analytics.ambiguity import (
    apply_resolved_slots,
    check_ambiguity,
    detect_clarification_resolution,
    slot_to_ref,
    slots_from_refs,
)
from analytics.models import AnalyticalExecutionPlan, AnalyticalQuery, StatisticsSpec


def _historical_aep(**params):
    return AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="historical",
            analysis_type="scalar",
            statistics=StatisticsSpec(
                operation="argmax",
                parameters=dict(params),
            ),
        ),
    )


def test_detect_clarification_resolution_daily_total():
    slots = detect_clarification_resolution("Use the daily total please")
    assert slots["peak_metric"] == "daily_total_mwh"


def test_detect_clarification_resolution_peak_hour():
    slots = detect_clarification_resolution("Peak hour's calendar date")
    assert slots["peak_metric"] == "peak_hourly_mw"


def test_check_ambiguity_peak_date_without_metric():
    msg = check_ambiguity(
        "Which day did PJM load peak in 2023?",
        _historical_aep(),
    )
    assert msg is not None
    assert "Peak hour" in msg or "daily total" in msg.lower()


def test_check_ambiguity_skips_when_slot_resolved():
    msg = check_ambiguity(
        "Which day did PJM load peak in 2023?",
        _historical_aep(),
        session_slots={"peak_metric": "daily_total_mwh"},
    )
    assert msg is None


def test_apply_resolved_slots_sets_aggregation():
    aep = apply_resolved_slots(
        _historical_aep(),
        {"peak_metric": "daily_total_mwh"},
    )
    params = aep.query.statistics.parameters
    assert params["aggregation"] == "daily_sum"
    assert params["peak_metric"] == "daily_total_mwh"


def test_check_ambiguity_vague_threshold_without_table():
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            statistics=StatisticsSpec(operation="probability", parameters={}),
        ),
    )
    msg = check_ambiguity(
        "What is the probability of crossing this threshold tomorrow?",
        aep,
    )
    assert msg is not None
    assert "threshold" in msg.lower()


def test_check_ambiguity_per_location_with_table():
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            statistics=StatisticsSpec(operation="probability", parameters={}),
        ),
    )
    refs = [
        {
            "key": "pjm_2023_daily_total_mwh",
            "kind": "location_threshold_table",
            "metric": "daily_total_mwh",
            "rows": [
                {"location_id": "rto", "location_name": "RTO", "value": 1000.0},
            ],
        }
    ]
    msg = check_ambiguity(
        "Probability each location exceeds its respective threshold tomorrow",
        aep,
        refs=refs,
    )
    assert msg is None


def test_slots_from_refs_roundtrip():
    ref = slot_to_ref("peak_metric", "daily_total_mwh")
    assert slots_from_refs([ref])["peak_metric"] == "daily_total_mwh"
