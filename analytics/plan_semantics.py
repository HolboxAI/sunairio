"""Infer aggregation grain and output shape from resolver context."""

from __future__ import annotations

import re
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
    if aggregation == "daily_peak":
        output_grain = output_grain or "day"

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
        if aggregation == "daily_peak" and analysis == "time_series":
            output_grain = "day"
        elif single_day and aggregation == "hourly" and op in ("probability", "prob"):
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

    consecutive_hours = detect_consecutive_hours(ctx)
    location_scope = detect_location_scope(ctx)
    peak_hour_prob = detect_peak_hour_probability(ctx)

    if peak_hour_prob:
        aggregation = "hourly_peak"
        output_grain = "hour"
        threshold_mode = "none"

    if consecutive_hours:
        aggregation = "consecutive_streak"
        if location_scope == "single" and loc_count > 1:
            location_scope = detect_location_scope(ctx)
        if output_grain == "hour" and not single_day:
            output_grain = "location" if loc_count > 1 else "single"

    if (
        op in ("probability", "prob", "exceedance")
        and intent in ("forecast", "probability")
        and not single_day
        and aggregation == "hourly"
        and consecutive_hours is None
        and output_grain == "hour"
    ):
        # Multi-day probability of an event in the window → period-level, not hourly series.
        if any(
            k in blob
            for k in (
                "consecutive",
                "in a row",
                "streak",
                "heat wave",
                "during the week",
                "during the period",
                "at some point",
            )
        ):
            output_grain = "location" if loc_count > 1 else "single"
            aggregation = aggregation or "period_event"

    return {
        "aggregation": aggregation,
        "output_grain": output_grain,
        "threshold_mode": threshold_mode,
        "location_count": loc_count,
        "single_day": single_day,
        "consecutive_hours": consecutive_hours,
        "location_scope": location_scope,
        "peak_hour_probability": bool(peak_hour_prob),
        "top_n": int(peak_hour_prob.get("top_n", 5)) if peak_hour_prob else 0,
    }


def _norm(val: Any) -> str:
    return str(val or "").strip().lower().replace(" ", "_")


def _word_to_int(word: str) -> int:
    mapping = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }
    return mapping.get(word.lower(), 0)


def _is_single_day(ctx: ResolverContext) -> bool:
    tf = ctx.timeframe
    if not tf or not tf.start or not tf.end:
        return False
    return str(tf.start)[:10] == str(tf.end)[:10]


def detect_consecutive_hours(ctx: ResolverContext) -> Optional[int]:
    """Hours in a consecutive run, from stats params or user wording."""
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    for key in (
        "consecutive_hours",
        "min_consecutive_hours",
        "min_duration_hours",
        "duration_hours",
        "streak_hours",
    ):
        val = params.get(key)
        if val is not None:
            try:
                n = int(float(val))
                if n >= 2:
                    return n
            except (TypeError, ValueError):
                pass

    blob = " ".join(
        [
            getattr(ctx, "user_message", "") or "",
            " ".join(str(n) for n in (ctx.aep.notes or [])),
            str(stats.get("operation") or ""),
        ]
    ).lower()

    m = re.search(r"(\d+)\s*\+?\s*consecutive\s*hours?", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"(one|two|three|four|five|six|seven|eight|nine|ten)\s*consecutive\s*hours?", blob)
    if m:
        return _word_to_int(m.group(1))
    m = re.search(r"consecutive\s*(\d+)\s*hours?", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*hours?\s*in\s*a\s*row", blob)
    if m:
        return int(m.group(1))
    if "heat wave" in blob or "prolonged" in blob:
        return 3
    return None


def detect_location_scope(ctx: ResolverContext) -> str:
    """joint | separate | ambiguous when multiple locations are involved."""
    loc_count = int(ctx.locations.count if ctx.locations else 0) or len(
        (ctx.locations.values if ctx.locations else None) or []
    )
    if loc_count <= 1:
        return "single"

    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    explicit = str(params.get("location_scope") or params.get("joint") or "").lower()
    if explicit in ("joint", "simultaneous", "together", "all"):
        return "joint"
    if explicit in ("separate", "per_location", "independent", "each"):
        return "separate"

    blob = (getattr(ctx, "user_message", "") or "").lower()
    if any(
        w in blob
        for w in (
            "simultaneously",
            "at the same time",
            "both at once",
            "together",
            "jointly",
            "same time",
        )
    ):
        return "joint"
    if any(w in blob for w in ("each location", "per location", "separately", "respectively")):
        return "separate"
    return "ambiguous"


def extract_top_n(params: Dict[str, Any], user_message: str) -> int:
    """How many ranked hours/locations to return (default 5)."""
    for key in ("top_n", "n_top", "limit", "n", "count"):
        val = params.get(key)
        if val is not None:
            try:
                n = int(float(val))
                if n >= 1:
                    return n
            except (TypeError, ValueError):
                pass

    msg = (user_message or "").lower()
    m = re.search(r"top\s+(\d+)", msg)
    if m:
        return int(m.group(1))
    m = re.search(
        r"\btop\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        msg,
    )
    if m:
        n = _word_to_int(m.group(1))
        if n:
            return n
    if "top five" in msg or "top 5" in msg:
        return 5
    if "top three" in msg or "top 3" in msg:
        return 3
    return 5


def detect_peak_hour_probability(ctx: ResolverContext) -> Optional[Dict[str, Any]]:
    """Probability that the daily peak falls in each hour-of-day (not fixed threshold)."""
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower().replace(" ", "_")
    user = (getattr(ctx, "user_message", None) or "").lower()
    notes = " ".join(str(n) for n in (ctx.aep.notes or [])).lower()
    viz = ctx.visualization or {}
    viz_text = " ".join(
        str(viz.get(k) or "")
        for k in ("legend", "y", "x", "notes")
    ).lower()
    if isinstance(viz.get("y"), list):
        viz_text += " " + " ".join(str(y) for y in viz["y"]).lower()

    mode = _norm(
        params.get("mode")
        or params.get("probability_mode")
        or params.get("metric")
        or ""
    )
    if mode in (
        "peak_hour",
        "peak_hour_of_day",
        "daily_peak_hour",
        "hour_of_peak",
        "peak_hour_probability",
    ):
        return {"top_n": extract_top_n(params, user)}

    blob = f"{user} {notes} {viz_text} {op} {_norm(params.get('aggregation'))}"

    if any(
        k in blob
        for k in (
            "peak-hour",
            "peak hour candidate",
            "falls in this hour",
            "daily peak",
            "peak-hour candidate",
            "hour of peak",
            "peak hour of",
        )
    ):
        if op in ("probability", "prob", "exceedance", "mode", "argmax") or "probabil" in blob:
            return {"top_n": extract_top_n(params, user)}

    peak_hour_ask = any(
        k in user
        for k in (
            "which hour",
            "what hour",
            "hour of the day",
            "hour is most likely",
            "most likely hour",
            "when is the peak",
            "when will the peak",
        )
    ) and any(k in user for k in ("peak", "maximum", "max ", "highest", "top"))

    if peak_hour_ask and ("probabil" in user or op in ("probability", "prob")):
        return {"top_n": extract_top_n(params, user)}

    if op in ("argmax", "mode") and any(
        k in blob for k in ("hour", "hour_of_day", "hour_beginning")
    ):
        if "probabil" in blob or op in ("probability", "prob"):
            return {"top_n": extract_top_n(params, user)}

    if _norm(params.get("aggregation")) == "hourly_peak" and op in ("probability", "prob"):
        if any(k in user for k in ("which hour", "most likely", "top")):
            return {"top_n": extract_top_n(params, user)}

    return None


def extract_top_n(params: Dict[str, Any], user_message: str) -> int:
    """How many ranked hours/locations to return (default 5)."""
    for key in ("top_n", "n_top", "limit", "n", "count"):
        val = params.get(key)
        if val is not None:
            try:
                n = int(float(val))
                if n >= 1:
                    return n
            except (TypeError, ValueError):
                pass

    msg = (user_message or "").lower()
    m = re.search(r"top\s+(\d+)", msg)
    if m:
        return int(m.group(1))
    m = re.search(
        r"\btop\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        msg,
    )
    if m:
        n = _word_to_int(m.group(1))
        if n:
            return n
    if "top five" in msg or "top 5" in msg:
        return 5
    if "top three" in msg or "top 3" in msg:
        return 3
    return 5


def detect_peak_hour_probability(ctx: ResolverContext) -> Optional[Dict[str, Any]]:
    """Probability that the daily peak falls in each hour-of-day (not fixed threshold)."""
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower().replace(" ", "_")
    user = (getattr(ctx, "user_message", None) or "").lower()
    notes = " ".join(str(n) for n in (ctx.aep.notes or [])).lower()
    viz = ctx.visualization or {}
    viz_text = " ".join(
        str(viz.get(k) or "")
        for k in ("legend", "y", "x", "notes")
    ).lower()
    if isinstance(viz.get("y"), list):
        viz_text += " " + " ".join(str(y) for y in viz["y"]).lower()

    mode = _norm(
        params.get("mode")
        or params.get("probability_mode")
        or params.get("metric")
        or ""
    )
    if mode in (
        "peak_hour",
        "peak_hour_of_day",
        "daily_peak_hour",
        "hour_of_peak",
        "peak_hour_probability",
    ):
        return {"top_n": extract_top_n(params, user)}

    blob = f"{user} {notes} {viz_text} {op} {_norm(params.get('aggregation'))}"

    if any(
        k in blob
        for k in (
            "peak-hour",
            "peak hour candidate",
            "falls in this hour",
            "daily peak",
            "peak-hour candidate",
            "hour of peak",
            "peak hour of",
        )
    ):
        if op in ("probability", "prob", "exceedance", "mode", "argmax") or "probabil" in blob:
            return {"top_n": extract_top_n(params, user)}

    peak_hour_ask = any(
        k in user
        for k in (
            "which hour",
            "what hour",
            "hour of the day",
            "hour is most likely",
            "most likely hour",
            "when is the peak",
            "when will the peak",
        )
    ) and any(k in user for k in ("peak", "maximum", "max ", "highest", "top"))

    if peak_hour_ask and ("probabil" in user or op in ("probability", "prob")):
        return {"top_n": extract_top_n(params, user)}

    if op in ("argmax", "mode") and any(
        k in blob for k in ("hour", "hour_of_day", "hour_beginning")
    ):
        if "probabil" in blob or op in ("probability", "prob"):
            return {"top_n": extract_top_n(params, user)}

    if _norm(params.get("aggregation")) == "hourly_peak" and op in ("probability", "prob"):
        if any(k in user for k in ("which hour", "most likely", "top")):
            return {"top_n": extract_top_n(params, user)}

    return None
