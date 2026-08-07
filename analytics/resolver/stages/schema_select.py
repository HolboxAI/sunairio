"""SchemaSelector — list schema names needed for Phase 2 LLM2."""

from __future__ import annotations

from analytics.models import ResolverContext


def resolve(ctx: ResolverContext) -> ResolverContext:
    schemas: list[str] = []
    routing = ctx.routing or {}
    category = (ctx.variable.category if ctx.variable else "").lower()

    if routing.get("metadata"):
        schemas.extend(["entities", "locations", "resources", "resource_types", "variables"])
    else:
        schemas.extend(["variables", "locations"])
        if routing.get("forecast_database") or routing.get("forecast_evolution"):
            if category == "weather":
                schemas.append("weather_forecast")
            else:
                schemas.append("energy_forecast")
        if routing.get("historical_database"):
            if category == "weather":
                schemas.append("historical_weather")
            else:
                schemas.append("historical_iso_load_gen")
        if routing.get("forecast_evolution"):
            schemas.append("forecast_archive")

    # Preserve order, unique
    seen = set()
    out = []
    for s in schemas:
        if s not in seen:
            seen.add(s)
            out.append(s)
    ctx.required_schema = out

    # Also normalize statistics / visualization onto ctx for REP build
    stats = ctx.aep.query.statistics
    operation = stats.operation or stats.type
    params = dict(stats.parameters or {})
    if stats.value is not None and "value" not in params:
        params["value"] = stats.value
    ctx.statistics = {
        "operation": operation,
        "parameters": params,
        "value": stats.value if stats.value is not None else params.get("value"),
    }

    viz = ctx.aep.query.visualization
    y_meanings = []
    for y in viz.y_axis or []:
        if isinstance(y, dict):
            y_meanings.append(y.get("meaning") or y.get("unit") or "")
        else:
            y_meanings.append(str(y))
    if not y_meanings and ctx.variable:
        y_meanings = [ctx.variable.display_name]

    x_meaning = ""
    if isinstance(viz.x_axis, dict):
        x_meaning = str(viz.x_axis.get("meaning") or "")
    if not x_meaning:
        x_meaning = "Forecast Time" if routing.get("forecast_database") else "Time"

    ctx.visualization = {
        "required": bool(viz.required),
        "chart": viz.chart_type or ("line" if viz.required else None),
        "x": x_meaning,
        "y": y_meanings[0] if y_meanings else "",
        "legend": viz.legend or (
            "Location" if ctx.locations and ctx.locations.count > 1 else None
        ),
        "unit": ctx.variable.unit if ctx.variable else "",
    }
    ctx.comparison = dict(ctx.aep.query.comparison or {})
    return ctx
