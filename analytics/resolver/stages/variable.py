"""VariableResolver — aliases → canonical variable + unit."""

from __future__ import annotations

from analytics.catalog import resolve_variable_name
from analytics.models import ResolvedVariable, ResolverContext


def resolve(ctx: ResolverContext) -> ResolverContext:
    dim = ctx.aep.query.variable
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    if not values:
        ctx.errors.append("Variable is required.")
        return ctx

    entry = resolve_variable_name(values[0], ctx.variable_catalog)
    if not entry:
        ctx.errors.append(
            f"Variable '{values[0]}' could not be resolved to a supported platform variable."
        )
        return ctx

    ctx.variable = ResolvedVariable(
        name=str(entry["variable"]),
        display_name=str(entry.get("display_name") or entry["variable"]),
        unit=str(entry.get("unit") or ""),
        category=str(entry.get("category") or ""),
    )
    return ctx
