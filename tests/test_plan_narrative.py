"""Confirm plan narrative and assumption questions."""

from __future__ import annotations

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
from analytics.plan_semantics import detect_consecutive_hours, infer_plan_semantics
from analytics.resolver.stages.confirmation import resolve as confirm_resolve


def _houston_dfw_ctx(*, user_message: str = "") -> ResolverContext:
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            statistics=StatisticsSpec(
                operation="probability",
                parameters={"threshold": 95, "direction": "above"},
            ),
            timeframe=TimeframeSpec(start="2025-07-20", end="2025-07-25"),
        ),
    )
    return ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[
            {"variable": "temp_2m", "display_name": "2 m Air Temperature", "unit": "°C"},
            {"variable": "temp_2m_gen", "display_name": "2 m Air Temperature (gen-weighted)", "unit": "°C"},
        ],
        entity=ResolvedEntity(
            id="1", name="ercot_generic", display_name="ERCOT", timezone="US/Central"
        ),
        variable=ResolvedVariable(
            name="temp_2m", display_name="2 m Air Temperature", unit="°F", category="weather",
            native_unit="°C",
            unit_conversion={"from": "°C", "to": "°F", "method": "linear"},
        ),
        locations=ResolvedLocations(
            mode="explicit",
            count=2,
            values=[{}, {}],
            label="Houston Load Zone, DFW",
        ),
        timeframe=ResolvedTimeframe(start="2025-07-20", end="2025-07-25"),
        initialization=ResolvedInitialization(
            mode="latest",
            resolved="2025-07-19T00:00:00Z",
            values=[],
            label="Latest Forecast",
        ),
        statistics={
            "operation": "probability",
            "parameters": {"threshold": 95, "direction": "above"},
        },
        routing={"forecast_database": True},
        required_schema=[],
        user_message=user_message,
    )


def test_detect_three_consecutive_hours_from_wording():
    ctx = _houston_dfw_ctx(
        user_message=(
            "probability that Houston and DFW would see three consecutive hours "
            "of temps above 95F in the following work week"
        )
    )
    assert detect_consecutive_hours(ctx) == 3


def test_plan_narrative_consecutive_hours_not_hourly_exceedance():
    ctx = _houston_dfw_ctx(
        user_message="three consecutive hours above 95F during next work week"
    )
    narrative = build_plan_narrative(ctx)
    assert "consecutive" in narrative.lower()
    assert "one result per location" in narrative.lower()
    assert "95°F" in narrative
    assert "35" in narrative  # converted °C threshold
    assert "stored in °C" in narrative
    assert "divide by 1000 to get probability" not in narrative.lower()


def test_plan_questions_include_variable_and_location_scope():
    ctx = _houston_dfw_ctx(
        user_message="three consecutive hours above 95F during next work week"
    )
    questions = build_plan_questions(ctx)
    joined = " ".join(questions).lower()
    assert "temp_2m_gen" in joined
    assert "2025-07-20" in joined or "window" in joined
    assert "each location" in joined or "together" in joined


def test_confirm_summary_includes_plan_fields():
    ctx = _houston_dfw_ctx(
        user_message="three consecutive hours above 95F during next work week"
    )
    confirm_resolve(ctx)
    assert ctx.summary is not None
    assert ctx.summary.plan_narrative
    assert ctx.summary.plan_questions
    sem = infer_plan_semantics(ctx)
    assert sem.get("consecutive_hours") == 3
