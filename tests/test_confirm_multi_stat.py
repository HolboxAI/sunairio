"""Confirm card representation for multi-stat comparisons."""

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
    VisualizationSpec,
)
from analytics.resolver.stages.confirmation import resolve as confirm_resolve


def test_confirm_representation_multi_from_legend():
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="comparison",
            statistics=StatisticsSpec(operation="multi", parameters={}),
            timeframe=TimeframeSpec(start="2026-08-17", end="2026-08-23"),
            comparison={"enabled": True, "dimensions": ["statistics"]},
            visualization=VisualizationSpec(
                required=True,
                chart_type="line",
                legend="P50 (median) | Mean | Trimmed Mean (P20–P80)",
            ),
        ),
    )
    ctx = ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        entity=ResolvedEntity(
            id="1", name="ercot_generic", display_name="ERCOT", timezone="US/Central"
        ),
        variable=ResolvedVariable(
            name="temp_2m", display_name="2 m Air Temperature", unit="°C", category="weather"
        ),
        locations=None,
        timeframe=ResolvedTimeframe(start="2026-08-17", end="2026-08-23"),
        initialization=None,
        statistics={"operation": "multi", "parameters": {}, "value": None},
        visualization={
            "required": True,
            "chart": "line",
            "x": "hour",
            "y": "temperature",
            "legend": "P50 (median) | Mean | Trimmed Mean (P20–P80)",
            "unit": "°C",
        },
        comparison={"enabled": True, "dimensions": ["statistics"]},
    )
    # confirmation.resolve fills missing required fields — use minimal pre-resolved ctx
    ctx.locations = ResolvedLocations(
        mode="explicit", count=1, values=[], label="Houston Load Zone"
    )
    ctx.initialization = ResolvedInitialization(
        mode="latest", resolved="2026-08-12T05:00:00Z", values=[], label="Latest Forecast"
    )
    ctx.routing = {"forecast_database": True}
    ctx.required_schema = []

    confirm_resolve(ctx)
    assert ctx.summary is not None
    assert ctx.summary.forecast_representation == "Multi"
    assert "side by side" in ctx.summary.computation_summary
    assert "3 values per hour" in ctx.summary.output_shape
