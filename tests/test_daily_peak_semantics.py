"""Daily peak aggregation — confirm copy and plan questions."""

from __future__ import annotations

from analytics.computation import build_computation_summary, format_output_shape
from analytics.models import (
    AnalyticalExecutionPlan,
    AnalyticalQuery,
    ResolvedEntity,
    ResolvedInitialization,
    ResolvedLocations,
    ResolvedTimeframe,
    ResolvedVariable,
    ResolverContext,
    StatisticsSpec,
    TimeframeSpec,
)
from analytics.plan_narrative import build_plan_narrative, build_plan_questions
from analytics.plan_semantics import infer_plan_semantics


def _daily_peak_ctx() -> ResolverContext:
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="time_series",
            statistics=StatisticsSpec(
                operation="percentile",
                value=50,
                parameters={"percentile": 50, "aggregation": "daily_peak"},
            ),
            timeframe=TimeframeSpec(start="2026-08-13", end="2030-12-31"),
        ),
    )
    return ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[
            {"variable": "net_demand", "display_name": "Net Demand", "unit": "MW"},
        ],
        entity=ResolvedEntity(
            id="1", name="ercot_generic", display_name="ERCOT", timezone="US/Central"
        ),
        variable=ResolvedVariable(
            name="net_demand",
            display_name="Net Demand",
            unit="MW",
            category="energy",
            native_unit="MW",
        ),
        locations=ResolvedLocations(mode="logical_group", count=1, values=[{}], label="RTO"),
        timeframe=ResolvedTimeframe(start="2026-08-13", end="2030-12-31"),
        initialization=ResolvedInitialization(
            mode="latest",
            resolved="2026-08-13T11:00:00Z",
            values=[],
            label="Latest Forecast",
        ),
        statistics={
            "operation": "percentile",
            "value": 50,
            "parameters": {"percentile": 50, "aggregation": "daily_peak"},
        },
        routing={"forecast_database": True},
        required_schema=[],
        user_message="Show the daily peak net_demand for ERCOT from now through the end of 2030.",
    )


def test_daily_peak_semantics_output_grain_is_day():
    ctx = _daily_peak_ctx()
    sem = infer_plan_semantics(ctx)
    assert sem.get("aggregation") == "daily_peak"
    assert sem.get("output_grain") == "day"


def test_daily_peak_computation_summary_path_first():
    ctx = _daily_peak_ctx()
    summary = build_computation_summary(ctx)
    assert "each path's daily peak" in summary.lower()
    assert "one row per day" in summary.lower()


def test_daily_peak_output_shape():
    ctx = _daily_peak_ctx()
    sem = infer_plan_semantics(ctx)
    shape = format_output_shape("time_series", ctx.timeframe, semantics=sem)
    assert "Daily time series" in shape


def test_daily_peak_plan_questions_suggest_hour_first_alternative():
    ctx = _daily_peak_ctx()
    questions = build_plan_questions(ctx)
    joined = " ".join(questions).lower()
    assert "median hourly forecast" in joined or "p50 at each hour" in joined


def test_daily_peak_plan_narrative_mentions_daily_peak():
    ctx = _daily_peak_ctx()
    narrative = build_plan_narrative(ctx)
    assert "daily peak" in narrative.lower()
