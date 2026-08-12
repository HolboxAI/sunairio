"""Infer aggregation grain and output shape from resolver context."""

from __future__ import annotations

from typing import Any, Dict, Optional

from analytics.models import ResolverContext


def infer_plan_semantics(ctx: ResolverContext) -> Dict[str, Any]:
    """Structured execution semantics for confirm copy (single source of truth)."""
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower().replace(" ", "_")
    analysis = (ctx.aep.query.analysis_type or "").lower().replace(" ", "_")
    intent = (ctx.aep.query.intent or "").lower()

    aggregation = _norm(params.get("aggregation") or params.get("temporal_aggregation"))
    output_grain = _norm(params.get("output_grain") or params.get("grain"))
    threshold_mode = _norm(params.get("threshold_mode"))

    loc_count = int(ctx.locations.count if ctx.locations else 0) or len(
        (ctx.locations.values if ctx.locations else None) or []
    )
    loc_count = max(loc_count, 1)

    single_day = _is_single_day(ctx)

    # Operation-name hints from LLM1 (e.g. argmax_date_by_daily_sum).
    if "daily_sum" in op or "daily_total" in op:
        aggregation = aggregation or "daily_sum"
    if "daily_max" in op or "peak_hour" in op:
        aggregation = aggregation or "hourly_peak"
    if "argmax" in op and "date" in op:
        output_grain = output_grain or "location"
        if "daily_sum" in op or aggregation == "daily_sum":
            aggregation = aggregation or "daily_sum"
        else:
            aggregation = aggregation or "hourly_peak"

    user_lower = (getattr(ctx, "user_message", None) or "").lower()
    notes_blob = " ".join(str(n) for n in (ctx.aep.notes or [])).lower()
    blob = f"{user_lower} {notes_blob} {op}"

    if any(k in blob for k in ("daily total", "daily_sum", "sum of 24", "24 hourly", "mwh")):
        aggregation = aggregation or "daily_sum"
    if any(k in blob for k in ("per location", "each location", "respective")):
        output_grain = output_grain or "location"
        threshold_mode = threshold_mode or "per_location"

    if intent in ("forecast", "probability") and op in ("probability", "prob", "exceedance"):
        if aggregation == "daily_sum" or (
            single_day and loc_count > 1 and analysis in ("probability", "scalar", "comparison")
        ):
            aggregation = aggregation or "daily_sum"
            output_grain = output_grain or "location"
            if loc_count > 1:
                threshold_mode = threshold_mode or "per_location"
        elif not aggregation:
            aggregation = "hourly"

    if intent in ("historical", "history"):
        if op in ("max", "min") and loc_count > 1:
            output_grain = output_grain or "location"
        if op in ("argmax", "argmin"):
            output_grain = output_grain or "location"
            if aggregation == "daily_sum":
                pass
            elif "daily_sum" in blob or "daily total" in blob:
                aggregation = "daily_sum"
            else:
                aggregation = aggregation or "hourly_peak"

    if analysis == "scalar" and loc_count > 1 and not output_grain:
        output_grain = "location"

    if not aggregation:
        if intent in ("historical", "history"):
            aggregation = "hourly" if op in ("argmax", "argmin") else "period"
        elif op in ("probability", "prob"):
            aggregation = "hourly"
        else:
            aggregation = "hourly"

    if not output_grain:
        if single_day and aggregation == "hourly" and op in ("probability", "prob"):
            output_grain = "hour"
        elif analysis == "time_series":
            output_grain = "hour"
        elif analysis == "scalar":
            output_grain = "location" if loc_count > 1 else "single"
        else:
            output_grain = "single"

    if threshold_mode == "per_location" or (
        isinstance(params.get("threshold"), dict)
        or isinstance(params.get("thresholds"), (list, dict))
    ):
        threshold_mode = "per_location"
    elif not threshold_mode:
        threshold_mode = "single" if params.get("threshold") is not None else "none"

    return {
        "aggregation": aggregation,
        "output_grain": output_grain,
        "threshold_mode": threshold_mode,
        "location_count": loc_count,
        "single_day": single_day,
    }


def _norm(val: Any) -> str:
    return str(val or "").strip().lower().replace(" ", "_")


def _is_single_day(ctx: ResolverContext) -> bool:
    tf = ctx.timeframe
    if not tf or not tf.start or not tf.end:
        return False
    return str(tf.start)[:10] == str(tf.end)[:10]
