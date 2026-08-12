"""Tier A/B UX improvements — computation, session context, preview rows, price."""

from __future__ import annotations

from analytics.computation import build_computation_summary, format_output_shape
from analytics.llm2.executor import format_answer_message, preview_row_limit
from analytics.llm2.parser import Llm2Plan
from analytics.models import (
    AnalyticalExecutionPlan,
    AnalyticalQuery,
    ConfirmationSummary,
    ResolvedEntity,
    ResolvedLocations,
    ResolvedTimeframe,
    ResolvedVariable,
    ResolverContext,
    StatisticsSpec,
    TimeframeSpec,
)
from analytics.price import parse_historical_price
from analytics.resolver.pipeline import resolve_aep
from analytics.resolver.voice import compose_confirm_message, prefer_human_confirm_message
from analytics.session_context import (
    build_session_context_block,
    infer_resolved_slots,
    looks_like_methodology_question,
    normalize_timeframe_expression,
)
from analytics.zero_row import diagnose_zero_rows


def _forecast_ctx(*, operation="percentile", value=50, analysis_type="time_series"):
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type=analysis_type,
            statistics=StatisticsSpec(operation=operation, value=value, parameters={}),
            timeframe=TimeframeSpec(start="2026-08-12", end="2026-08-12"),
        ),
    )
    ctx = ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        variable=ResolvedVariable(
            name="load",
            display_name="Electric Load",
            unit="MW",
            category="Energy",
        ),
        timeframe=ResolvedTimeframe(start="2026-08-12", end="2026-08-12"),
        statistics={"operation": operation, "value": value, "parameters": {}},
        routing={"forecast_database": True},
    )
    return ctx


def test_preview_row_limit_shows_full_day():
    assert preview_row_limit(24) == 24
    assert preview_row_limit(12) == 12
    assert preview_row_limit(200) == 48


def test_format_message_no_truncation_for_24_rows():
    rows = [[f"2026-08-12T{i:02d}:00:00Z", i] for i in range(24)]
    result = {"columns": ["hour", "val"], "rows": rows, "row_count": 24}
    plan = Llm2Plan(sql="SELECT 1", target="forecast")
    msg = format_answer_message(template_filled=None, result=result, plan=plan)
    assert "Showing 12 of 24" not in msg
    assert "Showing 24 of 24" not in msg
    assert msg.count("|") >= 24


def test_computation_summary_trimmed_mean():
    ctx = _forecast_ctx(operation="trimmed_mean", analysis_type="time_series")
    ctx.statistics = {
        "operation": "trimmed_mean",
        "parameters": {"trim_pct": 10},
        "value": None,
    }
    summary = build_computation_summary(ctx)
    assert "1000" in summary
    assert "10%" in summary or "10" in summary


def test_output_shape_scalar_vs_series():
    assert format_output_shape("scalar") == "Single summary value"
    assert "Hourly" in format_output_shape("time_series", ResolvedTimeframe(
        start="2026-08-12", end="2026-08-12"
    ))


def test_confirm_panel_fields_include_calculation():
    summary = ConfirmationSummary(
        analysis="Forecast (probability)",
        entity="PJM",
        locations="PJM, PJM Mid-Atlantic, PJM South, PJM West",
        forecast_horizon="2026-08-12 → 2026-08-12",
        initialization="Latest Forecast",
        initialization_resolved="2026-08-11T08:00:00Z",
        forecast_representation="Probability",
        chart="None",
        output_shape="One probability per location (4 values)",
        computation_summary=(
            "For each of the 4 location(s), sum the 24 hourly Electric Load values "
            "per ensemble path for the day, then count the share of paths above each "
            "location's own threshold."
        ),
        user_intent_echo="You want each location evaluated against its own threshold.",
        aggregation="daily_sum",
        output_grain="location",
        threshold_mode="per_location",
    )
    msg = compose_confirm_message(summary)
    assert "One probability per location" in summary.output_shape
    assert "daily total" in summary.computation_summary or "24 hourly" in summary.computation_summary


def test_prefer_human_confirm_is_short_for_inline_panel():
    summary = ConfirmationSummary(
        analysis="Forecast",
        entity="PJM",
        locations="RTO",
        forecast_horizon="2026-08-12",
        initialization="Latest",
        initialization_resolved="2026-08-11",
        forecast_representation="Probability",
        chart="None",
        computation_summary="Detailed steps here.",
    )
    from analytics.resolver.voice import prefer_human_confirm_message

    msg = prefer_human_confirm_message("Long LLM1 explanation with sort the 1000 paths", summary)
    assert "Review the plan below" in msg
    assert "sort the 1000" not in msg


def test_daily_sum_probability_output_shape():
    from analytics.models import ResolverContext, ResolvedLocations, ResolvedTimeframe
    from analytics.plan_semantics import infer_plan_semantics

    ctx = ResolverContext(
        aep=AnalyticalExecutionPlan(
            status="resolved",
            query=AnalyticalQuery(
                intent="forecast",
                analysis_type="probability",
                statistics=StatisticsSpec(operation="probability", parameters={}),
                timeframe=TimeframeSpec(start="2026-08-12", end="2026-08-12"),
            ),
        ),
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        locations=ResolvedLocations(mode="explicit", count=4, values=[{}, {}, {}, {}], label="4 zones"),
        timeframe=ResolvedTimeframe(start="2026-08-12", end="2026-08-12"),
        statistics={"operation": "probability", "parameters": {}},
        routing={"forecast_database": True},
        user_message="probability for each location for their respective 2023 max",
    )
    sem = infer_plan_semantics(ctx)
    shape = format_output_shape("probability", ctx.timeframe, semantics=sem)
    assert "per location" in shape.lower()
    summary = build_computation_summary(ctx)
    assert "24 hourly" in summary.lower() or "daily total" in summary.lower()


def test_session_context_includes_refs_and_slots():
    block = build_session_context_block(
        refs=[{"key": "pjm_peak", "value": 147187.49, "unit": "MW", "variable_label": "Peak load"}],
        history=[{"role": "user", "content": "use realtime LMP for PJM RTO"}],
    )
    assert "147,187" in block or "147187" in block
    assert "real_time" in block.lower() or "realtime" in block.lower()


def test_infer_price_type_from_history():
    slots = infer_resolved_slots(
        [{"role": "user", "content": "realtime LMP is fine for price"}]
    )
    assert slots.get("price_type") == "real_time LMP"


def test_parse_da_lmp_historical_price():
    parsed = parse_historical_price("da_lmp")
    assert parsed is not None
    assert parsed["column"] == "day_ahead"


def test_normalize_next_week_monday_alias():
    assert normalize_timeframe_expression("next_week_monday_to_sunday") == "next_week"


def test_methodology_question_detection():
    assert looks_like_methodology_question("how are you going to calculate this?")


def test_zero_row_diagnostics():
    hints = diagnose_zero_rows(
        {
            "locations": {"values": [{"location_name": "Houston Load Zone", "weather_sims_id": "houston"}]},
            "timeframe": {"start": "2026-08-17", "end": "2026-08-23"},
            "initialization": {"resolved": "2026-08-11T05:00:00Z"},
            "variable": {"name": "temp_2m", "category": "Weather"},
            "routing": {"forecast_database": True},
        },
        sql="SELECT * FROM weather_forecast_ensemble_short WHERE location = 'houston'",
    )
    assert any("no rows" in h.lower() for h in hints)
    assert any("houston" in h.lower() for h in hints)


def test_historical_price_resolver_path():
    aep = AnalyticalExecutionPlan.from_dict(
        {
            "status": "resolved",
            "query": {
                "intent": "historical",
                "analysis_type": "time_series",
                "entity": {"mode": "explicit", "values": ["PJM"]},
                "location": {"mode": "logical_group", "values": ["RTO"]},
                "variable": {"mode": "explicit", "values": ["day_ahead_lmp"]},
                "timeframe": {"mode": "explicit", "start": "2026-08-01", "end": "2026-08-01"},
                "initialization": {"mode": "none", "values": []},
                "statistics": {"operation": "mean"},
            },
        }
    )
    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=[
            {
                "entity_id": "2",
                "entity": "PJM",
                "shortname": "pjm_generic",
                "timezone": "US/Eastern",
            }
        ],
        latest_inits={},
        entity_catalog={
            "pjm_generic": {
                "portfolio": {
                    "resource_name": "RTO (1)",
                    "energy_sims_id": "1",
                    "weather_sims_id": "1",
                    "resource_type": "portfolio",
                },
                "resources": [],
            }
        },
        variable_catalog=[],
        entity_variables={"pjm_generic": {"variables": ["load"], "weather": [], "energy_by_resource_type": {}}},
        current_utc="2026-08-11T12:00:00Z",
        user_message="day ahead LMP for August 1",
        session_slots={},
    )
    assert not errors, errors
    assert rep is not None
    assert rep.variable.name == "historical_price"
    assert "historical_iso_prices" in rep.required_schema
    assert summary is not None
    assert summary.computation_summary
