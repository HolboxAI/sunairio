"""Plain-language computation summaries for confirm cards."""

from __future__ import annotations

from typing import Any, Dict, Optional

from analytics.comparison_series import (
    extract_comparison_series,
    series_summary_labels,
)
from analytics.models import ResolverContext
from analytics.multi_variable import is_variable_comparison, resolved_variables, variable_labels
from analytics.plan_semantics import infer_plan_semantics


def format_output_shape(
    analysis_type: str,
    timeframe: Optional[Any] = None,
    *,
    semantics: Optional[Dict[str, Any]] = None,
    comparison_labels: Optional[list] = None,
) -> str:
    """Human label for how results are delivered."""
    sem = semantics or {}
    at = (analysis_type or "").strip().lower().replace(" ", "_")
    agg = sem.get("aggregation") or ""
    grain = sem.get("output_grain") or ""
    loc_count = int(sem.get("location_count") or 1)
    single_day = bool(sem.get("single_day"))

    if grain == "location" and agg == "daily_sum" and at in ("probability", "prob"):
        n = loc_count
        return f"One probability per location ({n} value{'s' if n != 1 else ''})"
    if grain == "location" and at == "scalar":
        return f"One value per location ({loc_count} rows)"
    if grain == "location" and agg in ("hourly_peak", "period"):
        return f"One row per location ({loc_count} rows)"
    if at == "scalar":
        return "Single summary value"
    if comparison_labels and len(comparison_labels) >= 2:
        hours = _hourly_row_hint(timeframe)
        joined = ", ".join(comparison_labels)
        if hours and hours <= 168:
            return f"{len(comparison_labels)} values per hour ({joined}) — ~{hours} rows"
        return f"{len(comparison_labels)} values per hour ({joined})"
    if at in ("time_series", "distribution"):
        if agg == "daily_peak":
            return "Daily time series (one value per calendar day)"
        start = end = ""
        if timeframe is not None:
            start = getattr(timeframe, "start", "") or ""
            end = getattr(timeframe, "end", "") or ""
        if start and end and start != end:
            try:
                from datetime import date

                d0 = date.fromisoformat(str(start)[:10])
                d1 = date.fromisoformat(str(end)[:10])
                hours = (d1 - d0).days * 24 + 24
                if hours <= 168:
                    return f"Hourly time series (~{hours} values)"
            except ValueError:
                pass
        return "Hourly time series"
    if at in ("probability", "prob"):
        if agg == "daily_sum" and single_day:
            return "One probability per location" if loc_count > 1 else "Single daily probability"
        if single_day:
            return "Hourly time series (~24 values)"
        return "Hourly time series"
    if at == "ranking":
        return "Ranked list"
    if at == "comparison":
        if comparison_labels and len(comparison_labels) >= 2:
            hours = _hourly_row_hint(timeframe)
            joined = ", ".join(comparison_labels)
            if hours and hours <= 168:
                return f"{len(comparison_labels)} values per hour ({joined}) — ~{hours} rows"
            return f"{len(comparison_labels)} values per hour ({joined})"
        return "Comparison series or table"
    return "Analysis output"


def _hourly_row_hint(timeframe: Optional[Any]) -> Optional[int]:
    start = end = ""
    if timeframe is not None:
        start = getattr(timeframe, "start", "") or ""
        end = getattr(timeframe, "end", "") or ""
    if not (start and end):
        return None
    try:
        from datetime import date

        d0 = date.fromisoformat(str(start)[:10])
        d1 = date.fromisoformat(str(end)[:10])
        return (d1 - d0).days * 24 + 24
    except ValueError:
        return None


def build_computation_summary(ctx: ResolverContext) -> str:
    """Non-SQL explanation of how the statistic will be computed."""
    sem = infer_plan_semantics(ctx)
    stats = ctx.statistics or {}
    op = str(stats.get("operation") or "").lower()
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    value = stats.get("value")
    intent = (ctx.aep.query.intent or "").lower()
    analysis = (ctx.aep.query.analysis_type or "").lower()
    var_label = (ctx.variable.display_name if ctx.variable else "") or "the variable"
    agg = sem.get("aggregation")
    grain = sem.get("output_grain")
    threshold_mode = sem.get("threshold_mode")
    loc_count = int(sem.get("location_count") or 1)

    comparison_series = extract_comparison_series(ctx)
    if len(comparison_series) >= 2:
        return _build_multi_stat_summary(ctx, comparison_series, var_label)

    if is_variable_comparison(ctx):
        plan_vars = resolved_variables(ctx)
        if len(plan_vars) >= 2:
            return _build_multi_variable_summary(ctx, plan_vars, stats)

    if intent in ("historical", "history"):
        price_col = ctx.price_column or params.get("price_column")
        if price_col:
            col_label = "day-ahead" if price_col == "day_ahead" else "real-time"
            if analysis == "scalar":
                return (
                    f"Look up observed {col_label} LMP from historical price actuals "
                    f"for the selected location(s) and time range."
                )
            return (
                f"Return hourly observed {col_label} LMP from historical price actuals "
                f"for the selected location(s) and time range."
            )
        if agg == "daily_sum" and op in ("argmax", "argmin", "max"):
            return (
                f"For each location, sum observed hourly {var_label} (MW) into a daily "
                f"total (MWh) for every calendar day in the period, then find the day with "
                f"the highest total. Return one row per location."
            )
        if op in ("max", "min") and grain == "location":
            return (
                f"Find the {op} observed {var_label} from platform historical actuals "
                f"over the requested period — one value per location."
            )
        if op in ("max", "min"):
            return (
                f"Find the {op} observed {var_label} from platform historical actuals "
                f"over the requested period."
            )
        if op in ("argmax", "argmin") and agg == "hourly_peak":
            return (
                f"For each location, scan all hourly observations and find the single "
                f"peak hour; return that hour's calendar date (local time) and load value."
            )
        if op in ("argmax", "argmin"):
            return (
                f"Identify when observed {var_label} reached its "
                f"{'peak' if op == 'argmax' else 'minimum'} in the requested period."
            )
        if op in ("mean", "average", "avg"):
            return (
                f"Average observed {var_label} from historical actuals across all hours "
                f"in the requested period."
            )
        return f"Query observed {var_label} from platform historical actuals."

    if op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = _num(params.get("trim_pct") or params.get("trim") or 10, default=10)
        mid_pct = max(0, 100 - 2 * int(trim))
        if analysis == "scalar":
            return (
                f"For each hour in range, sort the 1000 forecast paths for {var_label}, "
                f"drop the lowest and highest {int(trim)}% of paths, average the middle "
                f"~{mid_pct}% (~{int(1000 * mid_pct / 100)} paths). Then average those "
                f"hourly trimmed means across the period for one daily number."
            )
        return (
            f"For each hour, sort the 1000 forecast paths for {var_label}, drop the "
            f"lowest and highest {int(trim)}% of paths, and average the middle ~{mid_pct}%."
        )

    if op in ("percentile", "p", "median", "p50") or (
        op in ("median", "p50") and value in (50, "50", 50.0)
    ):
        p = _num(value, default=50)
        if agg == "daily_peak":
            return (
                f"For each calendar day (local time), find each path's daily peak "
                f"(MAX {var_label} within that day), then take P{int(p)} across "
                f"the 1000 paths — one row per day."
            )
        if analysis == "scalar":
            return (
                f"For each hour, take the P{int(p)} (median path) of {var_label} across "
                f"the 1000 ensemble paths, then average those hourly values across "
                f"the period."
            )
        return (
            f"For each forecast hour, sort the 1000 paths for {var_label} and take "
            f"the P{int(p)} value."
        )

    if op in ("mean", "average", "avg"):
        if analysis == "scalar":
            return (
                f"For each hour, average {var_label} across all 1000 ensemble paths, "
                f"then average those hourly means across the period for one summary value."
            )
        return (
            f"For each forecast hour, compute the arithmetic mean of {var_label} "
            f"across all 1000 ensemble paths."
        )

    if op in ("probability", "prob", "exceedance"):
        if sem.get("peak_hour_probability"):
            top_n = int(sem.get("top_n") or 5)
            tz = (ctx.entity.timezone if ctx.entity else "") or "local time"
            return (
                f"For each of the 1000 ensemble paths, find the hour with the highest "
                f"{var_label} that day (the path's daily peak hour). Count how many paths "
                f"peak in each hour, divide by 1000, and return the top {top_n} hours "
                f"by probability (Hour Beginning in {tz}). No fixed MW threshold applies."
            )
        direction = (params.get("direction") or "above").lower()
        dir_word = "below" if direction == "below" else "above"
        consecutive = sem.get("consecutive_hours")
        if consecutive:
            return (
                f"For each location, scan each of the 1000 ensemble paths hour-by-hour, "
                f"flag paths with at least one run of {consecutive}+ consecutive hours "
                f"{dir_word} the threshold, and report the share of paths as a percentage."
            )
        if agg == "daily_sum":
            th = (
                "each location's own threshold"
                if threshold_mode == "per_location"
                else "the threshold"
            )
            return (
                f"For each of the {loc_count} location(s), sum the 24 hourly {var_label} "
                f"values per ensemble path for the day, then count the share of the 1000 "
                f"paths where that daily total is {dir_word} {th}. One probability per location."
            )
        threshold = params.get("threshold")
        th_txt = f"{threshold}" if threshold is not None else "the threshold"
        return (
            f"For each hour, count how many of the 1000 paths have {var_label} "
            f"{dir_word} {th_txt}, divide by 1000 to get probability."
        )

    if op in ("prediction_interval", "interval"):
        lo = params.get("low") or params.get("lower") or params.get("from")
        hi = params.get("high") or params.get("upper") or params.get("to")
        if lo is not None and hi is not None:
            return (
                f"For each hour, return the P{lo}–P{hi} band of {var_label} "
                f"across the 1000 paths."
            )
        return f"Return a percentile band of {var_label} across the 1000 paths."

    if op in ("max", "min"):
        return (
            f"For each hour, take the {op} of {var_label} across the 1000 ensemble paths."
        )

    return f"Compute {var_label} from the latest forecast ensemble for the requested period."


def _build_multi_stat_summary(
    ctx: ResolverContext,
    series: list,
    var_label: str,
) -> str:
    """Plain-language steps for median/mean/trimmed-mean side-by-side comparisons."""
    n = len(series)
    intro = (
        f"For each forecast hour in the period, compute {n} values side by side "
        f"from the 1000 ensemble paths for {var_label}:"
    )
    bullets = [_describe_stat_step(s) for s in series]
    hours = _hourly_row_hint(ctx.timeframe)
    if hours and hours <= 168:
        closing = (
            f"Each hour is one row with all {n} statistics as separate columns "
            f"(~{hours} rows; separate lines on the chart)."
        )
    else:
        closing = (
            f"Each hour is one row with all {n} statistics as separate columns "
            f"(separate lines on the chart)."
        )
    body = "\n".join(f"• {line}" for line in bullets)
    return f"{intro}\n{body}\n{closing}"


def _describe_stat_step(series_item: Any) -> str:
    label = series_item.short_label()
    op = str(series_item.operation or "").lower()
    if op in ("percentile", "p", "median", "p50"):
        p = _num(series_item.value, default=50)
        if p == 50:
            return (
                f"{label} — at each hour, sort the 1000 paths and take the middle value"
            )
        return (
            f"{label} — at each hour, sort the 1000 paths and take the P{int(p)} value"
        )
    if op in ("mean", "average", "avg"):
        return f"{label} — at each hour, arithmetic average across all 1000 paths"
    if op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = _num(series_item.trim_pct, default=20)
        mid_pct = max(0, 100 - 2 * int(trim))
        return (
            f"{label} — at each hour, drop the lowest and highest {int(trim)}% of paths, "
            f"then average the middle ~{mid_pct}%"
        )
    return f"{label} — computed across the 1000 ensemble paths at each hour"


def _build_multi_variable_summary(
    ctx: ResolverContext,
    plan_vars: list,
    stats: Dict[str, Any],
) -> str:
    """Plain-language steps for temp+load style multi-variable comparisons."""
    n = len(plan_vars)
    op = str(stats.get("operation") or "percentile").lower()
    value = stats.get("value")
    stat_phrase = _shared_stat_phrase(op, value, stats.get("parameters") or {})

    loc_label = ""
    if ctx.locations and ctx.locations.label:
        loc_label = f" at {ctx.locations.label}"

    intro = (
        f"For each forecast hour in the period, compute {n} variables side by side"
        f"{loc_label} from the latest ensemble initialization:"
    )
    bullets = []
    for var in plan_vars:
        table = "weather forecast" if (var.category or "").lower() == "weather" else "energy forecast"
        bullets.append(
            f"{var.display_name} — {stat_phrase} from the {table} tables "
            f"(`{var.name}`), using the location's "
            f"{'weather' if (var.category or '').lower() == 'weather' else 'energy'} sim id"
        )

    hours = _hourly_row_hint(ctx.timeframe)
    if hours and hours <= 168:
        closing = (
            f"Join the series on forecast hour so each row has all {n} variables "
            f"(~{hours} rows; separate lines on the chart)."
        )
    else:
        closing = (
            f"Join the series on forecast hour so each row has all {n} variables "
            f"(separate lines on the chart)."
        )
    body = "\n".join(f"• {line}" for line in bullets)
    return f"{intro}\n{body}\n{closing}"


def _shared_stat_phrase(op: str, value: Any, params: Dict[str, Any]) -> str:
    op = (op or "percentile").lower()
    if op in ("percentile", "p", "median", "p50"):
        p = _num(value, default=50)
        if p == 50:
            return "take P50 (median) across the 1000 paths at each hour"
        return f"take P{int(p)} across the 1000 paths at each hour"
    if op in ("mean", "average", "avg"):
        return "average across all 1000 paths at each hour"
    if op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = _num(params.get("trim_pct") or params.get("trim"), default=10)
        return f"trimmed mean (drop outer {int(trim)}% of paths) at each hour"
    return "compute the requested statistic across the 1000 paths at each hour"


def restate_user_intent(message: str, ctx: ResolverContext) -> str:
    """Short echo of what the user asked, when detectable from the message."""
    text = (message or "").strip()
    if not text:
        return ""
    lower = text.lower()
    stats = ctx.statistics or {}
    op = str(stats.get("operation") or "").lower()

    if "mid 80" in lower or "middle 80" in lower or "80%" in lower:
        return "You asked for the average of the middle 80% of ensemble paths (not the plain mean)."
    if "trimmed" in lower and op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        return "You asked for a trimmed mean — average after dropping extreme paths."
    if is_variable_comparison(ctx) and len(resolved_variables(ctx)) >= 2:
        labels = ", ".join(v.display_name for v in resolved_variables(ctx))
        return f"You asked to compare {labels} side by side for each hour."
    comparison_series = extract_comparison_series(ctx)
    if len(comparison_series) >= 2:
        labels = series_summary_labels(comparison_series)
        return f"You asked to compare {labels} side by side for each hour."
    if "whole day" in lower or "daily average" in lower or "daily total" in lower:
        if (ctx.aep.query.analysis_type or "").lower() == "scalar":
            return "You asked for one summary for the whole day, not an hourly breakdown."
    if "how" in lower and "calculat" in lower:
        return "You asked how this value will be calculated before proceeding."
    if "respective" in lower and "each" in lower and "location" in lower:
        return "You want each location evaluated against its own threshold from the prior result."
    return ""


def _num(raw: Any, *, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
