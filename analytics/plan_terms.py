"""Key resolution terms for confirm cards — one line per binding decision."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from analytics.models import ResolverContext
from analytics.plan_semantics import infer_plan_semantics
from analytics.units import resolve_threshold_context

_VARIABLE_GLOSS: Dict[str, str] = {
    "net_demand": "load minus wind and solar generation",
    "net_demand_plus_outages": "net demand plus unavailable non-renewable capacity",
    "load": "electric load (MW)",
    "temp_2m": "population-weighted 2 m air temperature",
    "temp_2m_gen": "generation-weighted 2 m air temperature",
    "gsi": "grid stress index",
}


def build_plan_terms(ctx: ResolverContext) -> List[str]:
    """Bullet list of how each query-resolution factor was interpreted."""
    sem = infer_plan_semantics(ctx)
    terms: List[str] = []
    var = ctx.variable

    if var and var.name:
        gloss = _VARIABLE_GLOSS.get(var.name, "")
        line = f"Variable: {var.display_name} (`{var.name}`)"
        if gloss:
            line += f" — {gloss}"
        if var.unit:
            native = getattr(var, "native_unit", None) or var.unit
            if native and native != var.unit:
                line += f"; results labeled in {var.unit} (stored as {native})"
            elif var.unit:
                line += f"; unit {var.unit}"
        terms.append(line)

    if sem.get("peak_hour_probability"):
        top_n = int(sem.get("top_n") or 5)
        terms.append(
            "Peak: the single hour with the highest value that calendar day within "
            "each ensemble path — not a fixed MW/MWh cutoff you specified."
        )
        terms.append(
            "Threshold: none — each path uses its own daily maximum hour as the "
            "reference, not a shared numeric level."
        )
        terms.append(
            "Probability: for each hour, the share of the 1000 paths whose daily "
            "peak occurs in that hour (expressed as a percentage)."
        )
        terms.append(
            f"Most likely hour: the hour with the highest probability across all 24 "
            f"Hour Beginning slots."
        )
        terms.append(f"Output: top {top_n} hours ranked by probability.")
        _append_time_and_location_terms(ctx, terms, sem)
        return terms

    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower()

    if op in ("probability", "prob", "exceedance"):
        tc = resolve_threshold_context(ctx)
        if tc:
            terms.append(f"Threshold: {tc.display_text} (used for exceedance counting).")
            if tc.conversion_applied:
                terms.append(
                    f"Unit conversion: compare stored values as {tc.native_text} "
                    f"while showing {tc.display_text} to you."
                )
        elif params.get("threshold") is not None:
            terms.append(f"Threshold: {params.get('threshold')}.")
        elif sem.get("threshold_mode") == "per_location":
            terms.append(
                "Threshold: each location's own value from a prior result in this conversation."
            )
        else:
            terms.append(
                "Threshold: not yet specified — confirm how the cutoff should be chosen."
            )
        if sem.get("consecutive_hours"):
            n = sem["consecutive_hours"]
            terms.append(
                f"Event: at least {n} consecutive hours meeting the threshold in the period."
            )

    _append_time_and_location_terms(ctx, terms, sem)
    return terms


def _append_time_and_location_terms(
    ctx: ResolverContext,
    terms: List[str],
    sem: Dict[str, Any],
) -> None:
    tf = ctx.timeframe
    if tf and tf.start:
        if tf.start == tf.end:
            terms.append(f"Day: {tf.start} (single calendar day, 24 hourly values).")
        elif tf.start and tf.end:
            terms.append(f"Period: {tf.start} → {tf.end}.")

    loc = ctx.locations
    if loc and loc.label:
        terms.append(f"Location: {loc.label}.")

    init = ctx.initialization
    if init and init.resolved:
        terms.append(f"Forecast initialization: {init.resolved}.")

    entity = ctx.entity
    if entity and entity.timezone and sem.get("peak_hour_probability"):
        terms.append(f"Hour labels: Hour Beginning in {entity.timezone} (local).")
