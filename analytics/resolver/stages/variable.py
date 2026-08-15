"""VariableResolver — aliases → canonical variable + entity/location availability gate."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Optional

from analytics.catalog import resolve_variable_name
from analytics.intent import is_awareness, is_metadata, needs_variable, normalize_intent
from analytics.models import ResolvedVariable, ResolverContext
from analytics.multi_variable import is_variable_comparison
from analytics.price import is_price_phrase, parse_historical_price
from analytics.units import enrich_variable_units

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

    intent = normalize_intent(ctx.aep.query.intent)
    if intent in ("historical", "history"):
        price_info = _resolve_historical_price(values[0], ctx)
        if price_info is not None:
            if price_info.get("error"):
                ctx.errors.append(price_info["error"])
                ctx.unresolved.add("variable")
                return ctx
            ctx.price_column = price_info["column"]
            ctx.variable = ResolvedVariable(
                name="historical_price",
                display_name=price_info["display_name"],
                unit=price_info.get("unit") or "$/MWh",
                category="Market",
            )
            return ctx

    multi = is_variable_comparison(ctx) or len(values) >= 2
    if multi:
        return _resolve_multi(ctx, values)

    return _resolve_single(ctx, values[0])


def _resolve_single(ctx: ResolverContext, raw: str) -> ResolverContext:
    resolved, err = _resolve_one(ctx, raw)
    if err:
        ctx.errors.append(err)
        ctx.unresolved.add("variable")
        return ctx
    ctx.variable = resolved
    ctx.variables = [resolved]
    return ctx


def _resolve_multi(ctx: ResolverContext, values: List[str]) -> ResolverContext:
    resolved: List[ResolvedVariable] = []
    seen: Set[str] = set()
    for raw in values:
        name_key = raw.lower().strip()
        if name_key in seen:
            continue
        seen.add(name_key)
        var, err = _resolve_one(ctx, raw)
        if err:
            ctx.errors.append(err)
            ctx.unresolved.add("variable")
            return ctx
        if var.name in {v.name for v in resolved}:
            continue
        resolved.append(var)

    if len(resolved) < 2:
        ctx.errors.append(
            "Which two or more variables should we compare? "
            "For example: temperature and load."
        )
        ctx.unresolved.add("variable")
        return ctx

    ctx.variables = resolved
    ctx.variable = resolved[0]
    return ctx


def _resolve_one(ctx: ResolverContext, raw: str) -> tuple[Optional[ResolvedVariable], str]:
    entry = resolve_variable_name(raw, ctx.variable_catalog)
    if not entry:
        return None, (
            f"I couldn't map '{raw}' to a supported platform variable. "
            "Try a catalog name like temperature, load, or wind speed."
        )

    name = str(entry["variable"])
    display = str(entry.get("display_name") or name)
    unit = str(entry.get("unit") or "")
    category = str(entry.get("category") or "")

    if "entity" not in ctx.unresolved:
        gate_error = _availability_error(ctx, name, display)
        if gate_error:
            return None, gate_error

    return enrich_variable_units(
        ResolvedVariable(
            name=name,
            display_name=display,
            unit=unit,
            category=category,
            native_unit=unit,
        ),
        ctx,
    ), ""


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


def _session_price_column(ctx: ResolverContext) -> Optional[str]:
    """Use clarify memory when the user already chose DA vs RT in this thread."""
    slots = getattr(ctx, "session_slots", None) or {}
    pt = str(slots.get("price_type") or "").lower()
    if "real" in pt:
        return "real_time"
    if "day" in pt and "ahead" in pt:
        return "day_ahead"
    return None


def _resolve_historical_price(raw: str, ctx: ResolverContext) -> Optional[Dict[str, Any]]:
    if not is_price_phrase(raw):
        return None
    parsed = parse_historical_price(raw)
    if parsed:
        return parsed
    column = _session_price_column(ctx)
    if column == "real_time":
        return {
            "column": "real_time",
            "display_name": "Real-Time LMP",
            "unit": "$/MWh",
        }
    if column == "day_ahead":
        return {
            "column": "day_ahead",
            "display_name": "Day-Ahead LMP",
            "unit": "$/MWh",
        }
    return {
        "error": (
            "Should I use day-ahead LMP or real-time LMP for this historical price lookup?"
        )
    }
