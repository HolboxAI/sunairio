"""Multi-variable comparison detection and REP helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from analytics.models import ResolvedVariable, ResolverContext


def is_variable_comparison(ctx: ResolverContext) -> bool:
    """True when the plan compares two or more variables side by side."""
    comparison = dict(ctx.comparison or {})
    if not comparison:
        comparison = dict(ctx.aep.query.comparison or {}) if ctx.aep and ctx.aep.query else {}

    values = [
        str(v).strip()
        for v in (ctx.aep.query.variable.values or [])
        if str(v).strip()
    ]
    dims = [str(d).lower() for d in (comparison.get("dimensions") or [])]

    if comparison.get("enabled") and ("variable" in dims or "variables" in dims):
        return len(values) >= 2 or len(ctx.variables or []) >= 2
    if len(values) >= 2:
        return True
    return len(ctx.variables or []) >= 2


def location_key_for_category(category: str) -> str:
    return "weather_sims_id" if (category or "").lower() == "weather" else "energy_sims_id"


def variable_spec_dict(var: ResolvedVariable) -> Dict[str, Any]:
    out = var.to_dict()
    out["location_key"] = location_key_for_category(var.category)
    return out


def resolved_variables(ctx: ResolverContext) -> List[ResolvedVariable]:
    if ctx.variables:
        return list(ctx.variables)
    if ctx.variable and ctx.variable.name:
        return [ctx.variable]
    return []


def variable_labels(ctx: ResolverContext) -> List[str]:
    labels: List[str] = []
    for var in resolved_variables(ctx):
        label = var.display_name or var.name
        if var.unit:
            label = f"{label} ({var.unit})"
        labels.append(label)
    return labels


def has_weather_variable(ctx: ResolverContext) -> bool:
    return any((v.category or "").lower() == "weather" for v in resolved_variables(ctx))


def categories_in_plan(ctx: ResolverContext) -> List[str]:
    return [(v.category or "").lower() for v in resolved_variables(ctx) if v.category]
