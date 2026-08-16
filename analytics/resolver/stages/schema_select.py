"""SchemaSelector — list schema names needed for Phase 2 LLM2."""

from __future__ import annotations

from analytics.models import ResolverContext
from analytics.multi_variable import categories_in_plan, is_variable_comparison, resolved_variables
from analytics.plan_semantics import infer_plan_semantics


def _is_numeric_threshold(raw) -> bool:
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)):
        return True
    text = str(raw).strip().replace(",", "")
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def resolve(ctx: ResolverContext) -> ResolverContext:
    schemas: list[str] = []
    routing = ctx.routing or {}
    categories = categories_in_plan(ctx) or [
        (ctx.variable.category if ctx.variable else "").lower()
    ]

    if routing.get("metadata"):
        schemas.extend(
            [
                "entities",
                "locations",
                "resources",
                "resource_types",
                "variables",
                "location_weights",
                "location_variables",
                "resource_variables",
            ]
        )
    else:
        schemas.extend(["variables", "locations"])
        if routing.get("forecast_database") or routing.get("forecast_evolution"):
            for category in categories:
                if category == "weather":
                    schemas.append("weather_forecast")
                else:
                    schemas.append("energy_forecast")
        if routing.get("historical_database"):
            if ctx.price_column or (
                ctx.variable and ctx.variable.name == "historical_price"
            ):
                schemas.append("historical_iso_prices")
            elif any(c == "weather" for c in categories):
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
    operation = (stats.operation or stats.type or "").strip().lower() or None
    params = dict(stats.parameters or {})
    value = stats.value
    if value is None:
        for key in ("value", "percentile", "p", "n"):
            if params.get(key) is not None:
                value = params.get(key)
                break
    # Routine central forecast: LLM1 may say "percentile" without a number, or
    # leave statistics empty — default to P50 so the confirm card never reads
    # "Percentile (None)".
    if not operation and not (ctx.routing or {}).get("metadata"):
        operation = "percentile"
        value = 50
    if operation in ("percentile", "p", "median", "p50") and value is None:
        value = 50
    if operation in ("median", "p50"):
        operation = "percentile"
        value = 50
    if value is not None:
        params.setdefault("value", value)
        if operation in ("percentile", "p"):
            params.setdefault("percentile", value)

    # Probability thresholds must be real numbers. Symbolic placeholders
    # (e.g. "2023_annual_peak_load_mw") must not reach the confirm card — that
    # path previously led the model to invent MW figures under awareness.
    if operation in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = params.get("trim_pct") or params.get("trim")
        if trim is None:
            params["trim_pct"] = 10

    if ctx.price_column:
        params["price_column"] = ctx.price_column

    semantics = infer_plan_semantics(ctx)
    for key in ("aggregation", "output_grain", "threshold_mode"):
        if semantics.get(key):
            params.setdefault(key, semantics[key])

    if operation in ("probability", "prob", "exceedance") and "statistics" not in ctx.unresolved:
        threshold = params.get("threshold")
        thresholds = params.get("thresholds")
        has_per_loc = isinstance(thresholds, dict) and bool(thresholds) and all(
            _is_numeric_threshold(v) for v in thresholds.values()
        )
        hist_src = params.get("threshold_source")
        has_historical_source = hist_src == "historical" or (
            isinstance(hist_src, dict)
            and str(hist_src.get("intent") or hist_src.get("source") or "").strip().lower()
            == "historical"
        )
        if (
            threshold is not None
            and not _is_numeric_threshold(threshold)
            and not has_per_loc
            and not has_historical_source
        ):
            ctx.errors.append(
                "The exceedance threshold still needs a real number from platform "
                "historical actuals (for example the 2023 max load in MW), or a "
                "numeric value you provide. I cannot use a named placeholder, and I "
                "will not invent the peak — ask me to look it up from observed history."
            )
            ctx.unresolved.add("statistics")

    ctx.statistics = {
        "operation": operation,
        "parameters": params,
        "value": value,
    }

    viz = ctx.aep.query.visualization
    y_meanings = []
    y_units: list[str] = []
    plan_vars = resolved_variables(ctx)
    if is_variable_comparison(ctx) and len(plan_vars) >= 2:
        y_meanings = [v.display_name for v in plan_vars]
        y_units = [v.unit for v in plan_vars]
    else:
        for y in viz.y_axis or []:
            if isinstance(y, dict):
                y_meanings.append(y.get("meaning") or y.get("unit") or "")
                y_units.append(str(y.get("unit") or ""))
            else:
                y_meanings.append(str(y))
        if not y_meanings and ctx.variable:
            y_meanings = [ctx.variable.display_name]
            y_units = [ctx.variable.unit or ""]

    x_meaning = ""
    if isinstance(viz.x_axis, dict):
        x_meaning = str(viz.x_axis.get("meaning") or "")
    if not x_meaning:
        x_meaning = "Forecast Time" if routing.get("forecast_database") else "Time"

    legend = viz.legend
    if not legend:
        if is_variable_comparison(ctx) and len(plan_vars) >= 2:
            legend = "Variable"
        elif ctx.locations and ctx.locations.count > 1:
            legend = "Location"

    chart_kind = str(viz.chart_type or "").lower()
    dual_axis = bool(
        is_variable_comparison(ctx)
        and len({u for u in y_units if u}) > 1
        and chart_kind != "scatter"
    )

    ctx.visualization = {
        "required": bool(viz.required),
        "chart": viz.chart_type or ("line" if viz.required else None),
        "x": x_meaning,
        "y": y_meanings if len(y_meanings) > 1 else (y_meanings[0] if y_meanings else ""),
        "y_units": y_units if len(y_units) > 1 else [],
        "legend": legend,
        "unit": y_units[0] if y_units else (ctx.variable.unit if ctx.variable else ""),
        "dual_axis": dual_axis,
    }
    ctx.comparison = dict(ctx.aep.query.comparison or {})
    return ctx
