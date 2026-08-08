"""VariableResolver — aliases → canonical variable + entity/location availability gate."""

from __future__ import annotations

from typing import Any, Dict, List, Set

from analytics.catalog import resolve_variable_name
from analytics.intent import is_awareness, is_metadata, needs_variable
from analytics.models import ResolvedVariable, ResolverContext

# Prefer these when suggesting alternatives after a rejection.
_SUGGEST_PREFERENCE = (
    "load",
    "net_demand",
    "temp_2m",
    "gsi",
    "wind_gen",
    "solar_gen",
    "wind_100m_mps",
    "ghi",
)


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

    name = str(entry["variable"])
    display = str(entry.get("display_name") or name)
    unit = str(entry.get("unit") or "")
    category = str(entry.get("category") or "")

    # Map the name even when entity is still open so the user gets both questions;
    # availability is only enforceable once entity (and ideally location) exist.
    if "entity" not in ctx.unresolved:
        gate_error = _availability_error(ctx, name, display)
        if gate_error:
            ctx.errors.append(gate_error)
            ctx.unresolved.add("variable")
            return ctx

    ctx.variable = ResolvedVariable(
        name=name,
        display_name=display,
        unit=unit,
        category=category,
    )
    return ctx


def _availability_error(ctx: ResolverContext, name: str, display: str) -> str:
    """Return a clarification message if var is not linked to entity / locations."""
    if not ctx.entity or not ctx.entity_variables:
        return ""

    avail = ctx.entity_variables.get(ctx.entity.name)
    if avail is None:
        # Entity not in the availability map — treat as no linked variables.
        avail = {
            "variables": [],
            "weather": [],
            "energy_by_resource_type": {},
        }

    linked: Set[str] = {str(v) for v in (avail.get("variables") or [])}
    weather: Set[str] = {str(v) for v in (avail.get("weather") or [])}
    by_rt: Dict[str, List[str]] = dict(avail.get("energy_by_resource_type") or {})
    entity_label = ctx.entity.display_name or ctx.entity.name

    if name not in linked:
        tips = _suggest(linked)
        tip_txt = f" Available here include: {', '.join(tips)}." if tips else ""
        return (
            f"{display} (`{name}`) isn't available for {entity_label}."
            f"{tip_txt}"
        )

    # Location / resource-type gate (only once locations are resolved).
    if "location" in ctx.unresolved or not ctx.locations or not ctx.locations.values:
        return ""

    if name in weather:
        # Weather vars are location_variables; any resolved site with weather_sims_id is fine.
        # If none carry weather ids, still allow at entity level (portfolio often has both).
        return ""

    allowed_types = {t.lower() for t in by_rt.get(name, [])}
    if not allowed_types:
        # Linked at entity level but no energy resource-type rows (shouldn't happen often).
        return ""

    present_types = {
        str(loc.get("resource_type") or "").lower()
        for loc in ctx.locations.values
        if loc.get("resource_type")
    }
    if not present_types:
        return ""

    if present_types.isdisjoint(allowed_types):
        loc_label = ctx.locations.label or "those locations"
        type_list = ", ".join(sorted(allowed_types))
        tips = _suggest(linked)
        tip_txt = f" Try a system/zone location, or another variable such as {', '.join(tips)}." if tips else ""
        return (
            f"{display} (`{name}`) isn't produced for {loc_label} on {entity_label} "
            f"(it applies to: {type_list})."
            f"{tip_txt}"
        )
    return ""


def _suggest(linked: Set[str], limit: int = 3) -> List[str]:
    ordered = [v for v in _SUGGEST_PREFERENCE if v in linked]
    if len(ordered) < limit:
        for v in sorted(linked):
            if v not in ordered:
                ordered.append(v)
            if len(ordered) >= limit:
                break
    return ordered[:limit]
