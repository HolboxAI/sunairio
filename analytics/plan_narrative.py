"""User-facing plan narrative and assumption questions for confirm cards."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from analytics.comparison_series import extract_comparison_series, series_summary_labels
from analytics.computation import build_computation_summary
from analytics.models import ResolverContext
from analytics.multi_variable import is_variable_comparison, resolved_variables, variable_labels
from analytics.plan_semantics import (
    detect_consecutive_hours,
    detect_location_scope,
    infer_plan_semantics,
)
from analytics.units import resolve_threshold_context

# Common variable pairs where an alternative may be worth confirming.
_VARIABLE_ALTERNATIVES: Dict[str, str] = {
    "temp_2m": "temp_2m_gen",
    "temp_2m_gen": "temp_2m",
    "ghi": "ghi_gen",
    "ghi_gen": "ghi",
}


def build_plan_narrative(ctx: ResolverContext) -> str:
    """First-person explanation of what will be queried and how."""
    intent = (ctx.aep.query.intent or "").lower()
    if intent in ("metadata", "metadata_lookup", "awareness"):
        return _metadata_narrative(ctx)

    opening = _opening_sentence(ctx)
    method = _method_paragraph(ctx)
    init = _initialization_sentence(ctx)
    unit_note = _unit_note(ctx)
    parts = [p for p in (opening, unit_note, method, init) if p]
    return "\n\n".join(parts)


def build_plan_questions(ctx: ResolverContext) -> List[str]:
    """Assumption checks the user can verify before confirming."""
    intent = (ctx.aep.query.intent or "").lower()
    if intent in ("metadata", "metadata_lookup", "awareness"):
        return []

    questions: List[str] = []
    sem = infer_plan_semantics(ctx)
    user_lower = (getattr(ctx, "user_message", None) or "").lower()
    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower()
    loc_count = int(sem.get("location_count") or 1)

    alt = _variable_alternative(ctx)
    if alt:
        questions.append(
            f"Does `{ctx.variable.name}` ({ctx.variable.display_name}) seem right? "
            f"`{alt['name']}` ({alt['label']}) is also commonly used in some scenarios."
        )

    tc = resolve_threshold_context(ctx)
    if tc and tc.conversion_applied and tc.source == "inferred":
        q = tc.confirm_question()
        if q:
            questions.append(q)

    if _timeframe_needs_confirmation(ctx, user_lower):
        questions.append(_timeframe_question(ctx))

    if op in ("probability", "prob", "exceedance"):
        if sem.get("peak_hour_probability"):
            questions.append(
                "Does using the path's peak hour (highest MW that day) match what you "
                "mean by peak net demand — rather than the daily total (MWh sum)?"
            )
        consecutive = sem.get("consecutive_hours")
        if consecutive:
            questions.append(
                f"Is counting paths with at least one run of {consecutive}+ consecutive "
                f"hours above the threshold the probability definition you want?"
            )
            if loc_count > 1 and sem.get("location_scope") == "ambiguous":
                questions.append(
                    "Should each location get its own probability, or do you want the "
                    "probability that all locations meet the condition together?"
                )
        elif sem.get("aggregation") == "daily_sum":
            questions.append(
                "Should probability be based on the daily total (sum of 24 hourly values) "
                "rather than hour-by-hour exceedance?"
            )
        elif sem.get("peak_hour_probability"):
            pass
        elif sem.get("output_grain") == "location" and loc_count > 1:
            if sem.get("location_scope") == "ambiguous":
                questions.append(
                    "Should each location get its own probability, or do you want the "
                    "probability that all locations meet the condition together?"
                )
        elif sem.get("aggregation") == "hourly" and not consecutive:
            questions.append(
                "Should probability be reported hour-by-hour, or as one number for the "
                "whole period (e.g. any hour / any streak in the window)?"
            )

    if is_variable_comparison(ctx) and len(resolved_variables(ctx)) >= 2:
        questions.append(
            "Should these variables be aligned on the same forecast hours in one table?"
        )

    comparison_series = extract_comparison_series(ctx)
    if len(comparison_series) >= 2 and not questions:
        labels = series_summary_labels(comparison_series)
        questions.append(
            f"Does comparing {', '.join(labels)} side by side for each hour match what you want?"
        )

    trim = params.get("trim_pct") or params.get("trim")
    if op in ("trimmed_mean", "trim_mean", "winsorized_mean") and trim is None:
        questions.append("What trim percentage should be dropped from each tail of the paths?")

    if _norm(params.get("aggregation")) == "daily_peak" and op in (
        "percentile",
        "p",
        "median",
        "p50",
    ):
        questions.append(
            "Alternatively, would you prefer the peak of the median hourly forecast "
            "(P50 at each hour, then MAX within each day) instead of the median of "
            "each path's daily peak?"
        )

    return questions


def _metadata_narrative(ctx: ResolverContext) -> str:
    entity = ctx.entity.display_name if ctx.entity else "the entity"
    loc = ctx.locations.label if ctx.locations else "the catalog"
    return f"I will look up {loc} for {entity} from platform metadata."


def _opening_sentence(ctx: ResolverContext) -> str:
    sem = infer_plan_semantics(ctx)
    stats = ctx.statistics or {}
    op = str(stats.get("operation") or "").lower()
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    intent = (ctx.aep.query.intent or "").lower()
    analysis = (ctx.aep.query.analysis_type or "").lower()

    loc_label = (ctx.locations.label if ctx.locations else "") or "the selected location(s)"
    var = ctx.variable
    var_phrase = f"{var.display_name} (`{var.name}`)" if var and var.name else "the requested variable"
    horizon = _horizon_phrase(ctx)
    entity = ctx.entity.display_name if ctx.entity else "the entity"

    if is_variable_comparison(ctx):
        labels = ", ".join(variable_labels(ctx))
        return (
            f"For {loc_label} under {entity}, I will compare "
            f"{labels} side by side for each forecast hour{horizon}."
        )

    comparison_series = extract_comparison_series(ctx)
    if len(comparison_series) >= 2:
        labels = ", ".join(series_summary_labels(comparison_series))
        return (
            f"For {loc_label} under {entity}, I will compute {labels} "
            f"for {var_phrase} at each forecast hour{horizon}."
        )

    if intent in ("historical", "history"):
        if op in ("probability", "prob"):
            return (
                f"For {loc_label}, I will estimate probability from historical "
                f"observations of {var_phrase}{horizon}."
            )
        return (
            f"For {loc_label}, I will query historical actuals for {var_phrase}"
            f"{horizon} under {entity}."
        )

    if op in ("probability", "prob", "exceedance"):
        if sem.get("peak_hour_probability"):
            top_n = int(sem.get("top_n") or 5)
            return (
                f"For {loc_label}, I will find which hour of the day is most likely to "
                f"contain the daily peak in {var_phrase}{horizon}, and show the "
                f"probability for each of the top {top_n} candidate hours."
            )

        direction = (params.get("direction") or "above").lower()
        dir_word = "below" if direction == "below" else "at or above"
        threshold = params.get("threshold")
        th_txt = _format_threshold(threshold, var, ctx)
        consecutive = sem.get("consecutive_hours")
        loc_count = int(sem.get("location_count") or 1)
        scope = sem.get("location_scope")

        if consecutive:
            streak = (
                f"at least {consecutive} consecutive hours {dir_word} {th_txt}"
            )
            if scope == "joint" and loc_count > 1:
                return (
                    f"For {loc_label}, I will find the probability that both "
                    f"locations experience {streak} during the same period{horizon}."
                )
            if loc_count > 1:
                return (
                    f"For {loc_label}, I will find the probability of {streak} "
                    f"in {var_phrase} during the period{horizon} — one result per location."
                )
            return (
                f"For {loc_label}, I will find the probability of {streak} "
                f"in {var_phrase}{horizon}."
            )

        if sem.get("aggregation") == "daily_sum":
            th_ref = (
                "each location's own threshold"
                if sem.get("threshold_mode") == "per_location"
                else th_txt
            )
            return (
                f"For {loc_label}, I will find the probability that the daily total "
                f"of {var_phrase} is {dir_word} {th_ref}{horizon}."
            )

        if sem.get("output_grain") == "location" and loc_count > 1:
            return (
                f"For {loc_label}, I will find the probability that {var_phrase} "
                f"is {dir_word} {th_txt}{horizon} — one result per location."
            )

        if params.get("threshold") is None and th_txt == "the threshold":
            return (
                f"For {loc_label}, I will compute forecast probabilities for "
                f"{var_phrase}{horizon}."
            )

        return (
            f"For {loc_label}, I will find the probability that {var_phrase} "
            f"is {dir_word} {th_txt}{horizon}."
        )

    if analysis == "scalar" and op in ("percentile", "p", "median", "p50", "mean", "average", "avg"):
        return (
            f"For {loc_label}, I will compute one summary value for {var_phrase}"
            f"{horizon} under {entity}."
        )

    if analysis in ("time_series", "comparison", "distribution"):
        if sem.get("aggregation") == "daily_peak":
            return (
                f"For {loc_label}, I will return the daily peak in {var_phrase}"
                f"{horizon} under {entity} — one value per calendar day."
            )
        return (
            f"For {loc_label}, I will return a forecast time series for {var_phrase}"
            f"{horizon} under {entity}."
        )

    return (
        f"For {loc_label} under {entity}, I will analyze {var_phrase}{horizon}."
    )


def _method_paragraph(ctx: ResolverContext) -> str:
    """How the statistic will be computed — derived from semantics, not generic hourly prob."""
    sem = infer_plan_semantics(ctx)
    stats = ctx.statistics or {}
    op = str(stats.get("operation") or "").lower()
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    intent = (ctx.aep.query.intent or "").lower()
    var_label = (ctx.variable.display_name if ctx.variable else "") or "the variable"
    loc_count = int(sem.get("location_count") or 1)

    if is_variable_comparison(ctx) or len(extract_comparison_series(ctx)) >= 2:
        return build_computation_summary(ctx)

    if intent in ("historical", "history"):
        return build_computation_summary(ctx)

    if op in ("probability", "prob", "exceedance"):
        if sem.get("peak_hour_probability"):
            top_n = int(sem.get("top_n") or 5)
            tz = (ctx.entity.timezone if ctx.entity else "") or "local time"
            return (
                f"There is no fixed MW threshold. For each of the 1000 ensemble paths, "
                f"I will find the hour with the highest {var_label} that day (that path's "
                f"daily peak hour). I will count how many paths peak in each hour and "
                f"divide by 1000 to get the probability the daily peak falls in that hour, "
                f"then return the {top_n} hours with the highest probabilities "
                f"(Hour Beginning in {tz})."
            )

        consecutive = sem.get("consecutive_hours")
        scope = sem.get("location_scope")
        if consecutive:
            if scope == "joint" and loc_count > 1:
                return (
                    f"Using the latest forecast initialization, I will scan each of the "
                    f"1000 ensemble paths hour-by-hour at both locations, flag paths where "
                    f"both locations have a run of {consecutive}+ consecutive hours above "
                    f"the threshold during the window, and report that share as a percentage."
                )
            return (
                f"Using the latest forecast initialization, I will scan each of the 1000 "
                f"ensemble paths hour-by-hour, flag paths that contain at least one run of "
                f"{consecutive}+ consecutive hours above the threshold, and report the share "
                f"of paths as a percentage"
                f"{'' if loc_count <= 1 else ' — one result per location'}."
            )
        if sem.get("aggregation") == "daily_sum":
            th = (
                "each location's own threshold"
                if sem.get("threshold_mode") == "per_location"
                else "the threshold"
            )
            return (
                f"For each of the {loc_count} location(s), I will sum the 24 hourly "
                f"{var_label} values per ensemble path for the day, count how many of the "
                f"1000 paths exceed {th}, and divide by 1000."
            )
        if sem.get("output_grain") == "location" and loc_count > 1 and sem.get("aggregation") != "hourly":
            return (
                f"For each location independently, I will evaluate all 1000 ensemble paths "
                f"over the period and count the share where the condition holds."
            )
        # Period-level single probability (common for multi-day windows)
        if sem.get("output_grain") in ("location", "single") and not sem.get("single_day"):
            if loc_count > 1:
                return (
                    f"For each location, I will evaluate all 1000 ensemble paths across the "
                    f"period and count the share where {var_label} meets the condition at "
                    f"least once in the window."
                )
            return (
                f"I will evaluate all 1000 ensemble paths across the period and count the "
                f"share where {var_label} meets the condition."
            )

    return build_computation_summary(ctx)


def _initialization_sentence(ctx: ResolverContext) -> str:
    init = ctx.initialization
    if not init:
        return ""
    label = (init.label or init.mode or "latest initialization").strip()
    resolved = init.resolved
    if resolved and str(resolved).upper() != "N/A":
        if label.lower() in ("latest", "latest forecast", "latest initialization"):
            return f"I will use the latest forecast initialization (resolved to {resolved})."
        return f"I will use initialization {label} (resolved to {resolved})."
    if label and label.upper() != "N/A":
        return f"I will use initialization {label}."
    return ""


def _horizon_phrase(ctx: ResolverContext) -> str:
    tf = ctx.timeframe
    if not tf or (not tf.start and not tf.end):
        return ""
    if tf.start == tf.end:
        return f" on {tf.start}"
    return f" during {tf.start} → {tf.end}"


def _unit_note(ctx: ResolverContext) -> str:
    tc = resolve_threshold_context(ctx)
    if not tc or not tc.conversion_applied:
        return ""
    var_name = ctx.variable.name if ctx.variable else ""
    return tc.plan_sentence(variable_name=var_name)


def _format_threshold(threshold: Any, var: Any, ctx: Optional[ResolverContext] = None) -> str:
    if ctx is not None:
        tc = resolve_threshold_context(ctx)
        if tc:
            return tc.display_text
    if threshold is None:
        return "the threshold"
    unit = (var.unit if var else "") or ""
    if unit and str(threshold).replace(".", "", 1).isdigit():
        return f"{threshold}{unit}"
    return str(threshold)


def _variable_alternative(ctx: ResolverContext) -> Optional[Dict[str, str]]:
    var = ctx.variable
    if not var or not var.name:
        return None
    alt_name = _VARIABLE_ALTERNATIVES.get(var.name)
    if not alt_name:
        return None
    for entry in ctx.variable_catalog or []:
        if str(entry.get("variable") or entry.get("name") or "") == alt_name:
            return {
                "name": alt_name,
                "label": str(entry.get("display_name") or alt_name),
            }
    return {"name": alt_name, "label": alt_name.replace("_", " ")}


def _timeframe_needs_confirmation(ctx: ResolverContext, user_lower: str) -> bool:
    tf = ctx.timeframe
    if not tf or not tf.start:
        return False
    relative_markers = (
        "next week",
        "work week",
        "following week",
        "this week",
        "tomorrow",
        "next ",
    )
    return any(m in user_lower for m in relative_markers)


def _timeframe_question(ctx: ResolverContext) -> str:
    tf = ctx.timeframe
    if tf and tf.start and tf.end and tf.start != tf.end:
        return f"Is the window {tf.start} → {tf.end} the period you had in mind?"
    if tf and tf.start:
        return f"Is {tf.start} the date you had in mind?"
    return "Does the time period look right?"


def _norm(val: Any) -> str:
    return str(val or "").strip().lower().replace(" ", "_")
