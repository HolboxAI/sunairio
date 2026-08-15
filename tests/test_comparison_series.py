"""Multi-stat comparison detection and chart inference."""

from __future__ import annotations

from analytics.chart_infer import infer_chart_from_rep
from analytics.comparison_series import extract_comparison_series
from analytics.computation import build_computation_summary, format_output_shape
from analytics.models import (
    AnalyticalExecutionPlan,
    AnalyticalQuery,
    ResolvedEntity,
    ResolvedTimeframe,
    ResolvedVariable,
    ResolverContext,
    StatisticsSpec,
    TimeframeSpec,
    VisualizationSpec,
)


def _comparison_ctx(*, legend: str | None = None, series=None):
    params = {}
    if series:
        params["series"] = series
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="comparison",
            statistics=StatisticsSpec(operation="multi", parameters=params),
            timeframe=TimeframeSpec(start="2026-08-17", end="2026-08-23"),
            comparison={"enabled": True, "dimensions": ["statistics"]},
            visualization=VisualizationSpec(
                required=True,
                chart_type="line",
                x_axis={"meaning": "hour"},
                y_axis=[{"meaning": "temperature", "unit": "°C"}],
                legend=legend,
            ),
        ),
    )
    ctx = ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        variable=ResolvedVariable(
            name="temp_2m",
            display_name="2 m Air Temperature",
            unit="°C",
            category="weather",
        ),
        entity=ResolvedEntity(
            id="1",
            name="ercot_generic",
            display_name="ERCOT",
            timezone="US/Central",
        ),
        timeframe=ResolvedTimeframe(start="2026-08-17", end="2026-08-23"),
        statistics={"operation": "multi", "parameters": params, "value": None},
        visualization={
            "required": True,
            "chart": "line",
            "x": "hour",
            "y": "temperature",
            "legend": legend,
            "unit": "°C",
        },
        comparison={"enabled": True, "dimensions": ["statistics"]},
    )
    return ctx


def test_extract_series_from_legend():
    legend = "P50 (median) | Mean | Trimmed Mean (P20–P80)"
    ctx = _comparison_ctx(legend=legend)
    series = extract_comparison_series(ctx)
    assert len(series) == 3
    assert series[0].operation == "percentile"
    assert series[1].operation == "mean"
    assert series[2].operation == "trimmed_mean"
    assert series[2].trim_pct == 20


def test_multi_stat_computation_summary():
    ctx = _comparison_ctx(
        legend="P50 (median) | Mean | Trimmed Mean (P20–P80)"
    )
    summary = build_computation_summary(ctx)
    assert "side by side" in summary
    assert "1000 ensemble paths" in summary
    assert "P50 (median)" in summary
    assert "arithmetic average" in summary
    assert "20%" in summary
    assert "separate columns" in summary


def test_output_shape_multi_stat_comparison():
    labels = ["P50 (median)", "Mean", "Trimmed Mean (P20–P80)"]
    shape = format_output_shape(
        "comparison",
        ResolvedTimeframe(start="2026-08-17", end="2026-08-23"),
        comparison_labels=labels,
    )
    assert "3 values per hour" in shape
    assert "P50 (median)" in shape
    assert "~168" in shape


def test_infer_chart_wide_format():
    rep = {
        "analysis_type": "comparison",
        "entity": {"timezone": "US/Central"},
        "variable": {"unit": "°C"},
        "visualization": {"required": True, "chart": "line"},
    }
    data = {
        "columns": ["valid_datetime", "p50", "mean", "trimmed_mean"],
        "rows": [
            ["2026-08-17T05:00:00Z", 24.1, 24.5, 24.2],
            ["2026-08-17T06:00:00Z", 24.0, 24.3, 24.1],
        ],
    }
    applicable, details, tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details["x_axis"] == ["valid_datetime"]
    assert details["y_axis"] == ["p50", "mean", "trimmed_mean"]
    assert tz == "US/Central"
