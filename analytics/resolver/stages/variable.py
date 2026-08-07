"""VariableResolver — aliases → canonical variable + unit."""

from __future__ import annotations

from analytics.catalog import resolve_variable_name
from analytics.intent import is_awareness, is_metadata, needs_variable
from analytics.models import ResolvedVariable, ResolverContext


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = ctx.aep.query.intent
    if is_awareness(intent) or is_metadata(intent) or not needs_variable(intent):
        # Metadata / awareness do not require a forecast variable
        ctx.variable = ResolvedVariable(
            name="",
            display_name="N/A",
            unit="",
            category="",
        )
        return ctx

    dim = ctx.aep.query.variable
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    if not values:
        ctx.errors.append(
            "Which variable should we analyze? "
            "For example: temperature, load, wind speed, or solar generation."
        )
        ctx.unresolved.add("variable")
        return ctx

    entry = resolve_variable_name(values[0], ctx.variable_catalog)
    if not entry:
        ctx.errors.append(
            f"I couldn't map '{values[0]}' to a supported platform variable. "
            "Try a catalog name like temperature, load, or wind speed."
        )
        ctx.unresolved.add("variable")
        return ctx

    ctx.variable = ResolvedVariable(
        name=str(entry["variable"]),
        display_name=str(entry.get("display_name") or entry["variable"]),
        unit=str(entry.get("unit") or ""),
        category=str(entry.get("category") or ""),
    )
    return ctx
