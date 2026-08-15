"""Relative timeframe resolution including named weekdays."""

from __future__ import annotations

from analytics.models import (
    AnalyticalExecutionPlan,
    AnalyticalQuery,
    ResolvedEntity,
    ResolverContext,
    TimeframeSpec,
)
from analytics.resolver.stages.time import _local_now, _resolve_relative, resolve as resolve_time


def _ctx(*, expression: str, current_utc: str = "2026-08-13T14:00:00Z", intent: str = "forecast"):
    return ResolverContext(
        aep=AnalyticalExecutionPlan(
            status="resolved",
            query=AnalyticalQuery(
                intent=intent,
                timeframe=TimeframeSpec(mode="relative", expression=expression),
            ),
        ),
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        entity=ResolvedEntity(
            id="2",
            name="pjm_generic",
            display_name="PJM",
            timezone="US/Eastern",
        ),
        current_utc=current_utc,
    )


def test_this_wednesday_on_thursday():
    # Thu Aug 13 2026 → this week's Wednesday is Aug 12
    ctx = _ctx(expression="this_wednesday")
    bounds = _resolve_relative("this_wednesday", _local_now(ctx))
    assert bounds == ("2026-08-12", "2026-08-12")


def test_next_wednesday_on_thursday():
    ctx = _ctx(expression="next_wednesday")
    bounds = _resolve_relative("next_wednesday", _local_now(ctx))
    assert bounds == ("2026-08-19", "2026-08-19")


def test_this_wednesday_on_wednesday():
    ctx = _ctx(expression="this_wednesday", current_utc="2026-08-12T16:00:00Z")
    bounds = _resolve_relative("this_wednesday", _local_now(ctx))
    assert bounds == ("2026-08-12", "2026-08-12")


def test_resolve_time_this_wednesday_integration():
    ctx = _ctx(expression="this_wednesday")
    resolve_time(ctx)
    assert not ctx.errors
    assert ctx.timeframe is not None
    assert ctx.timeframe.start == "2026-08-12"
    assert ctx.timeframe.end == "2026-08-12"


def test_normalize_alias_this_wed():
    ctx = _ctx(expression="this_wed")
    bounds = _resolve_relative("this_wed", _local_now(ctx))
    assert bounds == ("2026-08-12", "2026-08-12")
