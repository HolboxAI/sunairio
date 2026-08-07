"""ConfirmationBuilder — user-facing summary + final REP assembly."""

from __future__ import annotations

from analytics.models import (
    ConfirmationSummary,
    ResolvedExecutionPlan,
    ResolverContext,
)


def _format_horizon(ctx: ResolverContext) -> str:
    tf = ctx.timeframe
    if not tf or (not tf.start and not tf.end):
        return "N/A"
    if tf.start == tf.end:
        return tf.start
    return f"{tf.start} → {tf.end}"


def _format_locations(ctx: ResolverContext) -> str:
    loc = ctx.locations
    if not loc:
        return "N/A"
    if loc.mode == "logical_group":
        return f"{loc.label} ({loc.count})"
    if loc.mode == "metadata_query":
        return loc.label or "Metadata lookup"
    return loc.label or f"{loc.count} location(s)"


def _format_representation(stats: dict) -> str:
    op = (stats.get("operation") or "").lower()
    value = stats.get("value")
    params = stats.get("parameters") or {}
    if value is None:
        value = params.get("value")
    if op in ("percentile", "p"):
        try:
            n = int(value)
            if n == 50:
                return "Median (P50)"
            return f"P{n}"
        except (TypeError, ValueError):
            return f"Percentile ({value})"
    if op in ("median", "p50"):
        return "Median (P50)"
    if op in ("mean", "average"):
        return "Mean"
    if op in ("prediction_interval", "interval"):
        return "Prediction Interval"
    if op:
        return op.replace("_", " ").title()
    return "Unspecified"


def _format_chart(viz: dict) -> str:
    if not viz.get("required") and not viz.get("chart"):
        return "None"
    chart = (viz.get("chart") or "line").title()
    parts = [chart]
    if viz.get("x"):
        parts.append(f"X: {viz['x']}")
    if viz.get("y"):
        unit = f" ({viz['unit']})" if viz.get("unit") else ""
        parts.append(f"Y: {viz['y']}{unit}")
    if viz.get("legend"):
        parts.append(f"Legend: {viz['legend']}")
    return " · ".join(parts)


def resolve(ctx: ResolverContext) -> ResolverContext:
    if ctx.errors:
        return ctx
    if not ctx.entity or not ctx.variable or not ctx.locations or not ctx.timeframe:
        ctx.errors.append("Resolved plan is incomplete.")
        return ctx
    if not ctx.initialization:
        ctx.errors.append("Initialization was not resolved.")
        return ctx

    intent = (ctx.aep.query.intent or "forecast").replace("_", " ").title()
    analysis_type = ctx.aep.query.analysis_type or "time_series"

    init = ctx.initialization
    init_resolved = init.resolved or (
        ", ".join(init.values) if init.values else "N/A"
    )

    ctx.summary = ConfirmationSummary(
        analysis=f"{intent} ({analysis_type.replace('_', ' ')})",
        entity=ctx.entity.display_name,
        locations=_format_locations(ctx),
        forecast_horizon=_format_horizon(ctx),
        initialization=init.label or init.mode,
        initialization_resolved=str(init_resolved),
        forecast_representation=_format_representation(ctx.statistics),
        chart=_format_chart(ctx.visualization),
        notes=list(ctx.aep.notes or []),
    )

    ctx.rep = ResolvedExecutionPlan(
        intent=(ctx.aep.query.intent or "forecast"),
        analysis_type=analysis_type,
        entity=ctx.entity,
        locations=ctx.locations,
        variable=ctx.variable,
        timeframe=ctx.timeframe,
        initialization=ctx.initialization,
        statistics=dict(ctx.statistics),
        routing=dict(ctx.routing),
        required_schema=list(ctx.required_schema),
        visualization=dict(ctx.visualization),
        comparison=dict(ctx.comparison),
        notes=list(ctx.aep.notes or []),
    )
    return ctx
