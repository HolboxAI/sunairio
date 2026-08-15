"""Peak-hour-of-day probability confirm copy."""

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
from analytics.plan_semantics import detect_peak_hour_probability, infer_plan_semantics
from analytics.plan_terms import build_plan_terms
from analytics.resolver.stages.confirmation import resolve as confirm_resolve


def _pjm_peak_hour_ctx(*, user_message: str) -> ResolverContext:
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            statistics=StatisticsSpec(
                operation="probability",
                parameters={},
            ),
            timeframe=TimeframeSpec(
                mode="relative",
                expression="this_wednesday",
                start="2026-08-19",
                end="2026-08-19",
            ),
        ),
    )
    return ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        entity=ResolvedEntity(
            id="2", name="pjm_generic", display_name="PJM", timezone="US/Eastern"
        ),
        variable=ResolvedVariable(
            name="net_demand",
            display_name="Net Demand",
            unit="MW",
            category="Energy",
        ),
        locations=ResolvedLocations(mode="logical_group", count=1, values=[], label="RTO"),
        timeframe=ResolvedTimeframe(start="2026-08-19", end="2026-08-19"),
        initialization=ResolvedInitialization(
            mode="latest",
            resolved="2026-08-13T03:00:00Z",
            values=[],
            label="Latest Forecast",
        ),
        statistics={"operation": "probability", "parameters": {}},
        visualization={
            "required": True,
            "chart": "bar",
            "x": "Hour Beginning (local ET)",
            "y": "Probability that daily peak net demand falls in this hour (%)",
            "legend": "Top 5 peak-hour candidates",
        },
        routing={"forecast_database": True},
        required_schema=[],
        user_message=user_message,
    )


def test_detect_peak_hour_probability_from_user_message():
    ctx = _pjm_peak_hour_ctx(
        user_message=(
            "Which hour of the day is most likely to have the peak net demand in PJM "
            "this Wednesday? And what is the probability for each of the top five hours?"
        )
    )
    assert detect_peak_hour_probability(ctx) is not None
    sem = infer_plan_semantics(ctx)
    assert sem.get("peak_hour_probability") is True
    assert sem.get("top_n") == 5


def test_plan_narrative_peak_hour_no_threshold_exceedance():
    msg = (
        "Which hour of the day is most likely to have the peak net demand in PJM "
        "this Wednesday? And what is the probability for each of the top five hours?"
    )
    ctx = _pjm_peak_hour_ctx(user_message=msg)
    narrative = build_plan_narrative(ctx)
    assert "no fixed mw threshold" in narrative.lower()
    assert "top 5" in narrative.lower()
    assert "at or above the threshold" not in narrative.lower()
    assert "have Net Demand above the threshold" not in narrative.lower()


def test_plan_terms_explain_peak_and_threshold():
    msg = (
        "Which hour of the day is most likely to have the peak net demand in PJM "
        "this Wednesday? And what is the probability for each of the top five hours?"
    )
    ctx = _pjm_peak_hour_ctx(user_message=msg)
    terms = build_plan_terms(ctx)
    joined = " ".join(terms).lower()
    assert "threshold: none" in joined
    assert "net_demand" in joined or "net demand" in joined
    assert "peak" in joined


def test_plan_questions_peak_hour_definition():
    msg = (
        "Which hour of the day is most likely to have the peak net demand in PJM "
        "this Wednesday? And what is the probability for each of the top five hours?"
    )
    ctx = _pjm_peak_hour_ctx(user_message=msg)
    questions = build_plan_questions(ctx)
    assert any("peak hour" in q.lower() for q in questions)


def test_confirm_summary_includes_plan_terms():
    msg = (
        "Which hour of the day is most likely to have the peak net demand in PJM "
        "this Wednesday? And what is the probability for each of the top five hours?"
    )
    ctx = _pjm_peak_hour_ctx(user_message=msg)
    confirm_resolve(ctx)
    assert ctx.summary is not None
    assert ctx.summary.plan_terms
    assert any("Threshold: none" in t for t in ctx.summary.plan_terms)
