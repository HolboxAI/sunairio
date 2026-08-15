"""LocationResolver — expand logical groups / resolve explicit names."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from analytics.intent import is_awareness, is_metadata
from analytics.models import ResolvedLocations, ResolverContext
from analytics.text_match import normalize, phrase_overlap
from data import metadata_db

_LOGICAL_MAP = {
    "all load zones": ("zone", "load"),
    "load zones": ("zone", "load"),
    "all solar zones": ("solar_zone", "solar"),
    "solar zones": ("solar_zone", "solar"),
    "all wind zones": ("wind_zone", "wind"),
    "wind zones": ("wind_zone", "wind"),
    "all weather zones": ("wx_zone",),
    "weather zones": ("wx_zone",),
    "all cdr zones": ("cdr_zone",),
    "cdr zones": ("cdr_zone",),
    "rto": ("portfolio",),
    "iso": ("portfolio",),
    "system": ("portfolio",),
}


def _catalog_resources(ctx: ResolverContext) -> List[Dict[str, Any]]:
    if not ctx.entity:
        return []
    bucket = ctx.entity_catalog.get(ctx.entity.name) or {}
    return list(bucket.get("resources") or [])


def _resources_by_types(
    resources: List[Dict[str, Any]], resource_types: List[str]
) -> List[Dict[str, Any]]:
    wanted = {t.lower() for t in resource_types if t}
    out = []
    for r in resources:
        if (r.get("resource_type") or "").lower() in wanted:
            out.append(
                {
                    "location_name": r.get("resource_name") or "",
                    "weather_sims_id": r.get("weather_sims_id") or "",
                    "energy_sims_id": r.get("energy_sims_id") or "",
                    "resource_type": r.get("resource_type") or "",
                }
            )
    return out


def _as_location(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "location_name": r.get("resource_name") or "",
        "weather_sims_id": r.get("weather_sims_id") or "",
        "energy_sims_id": r.get("energy_sims_id") or "",
        "resource_type": r.get("resource_type") or "",
    }


# Aggregation level used to break ties: a bare "Houston" means the load zone,
# not the CDR zone or a nearby weather station.
_TYPE_PRIORITY = {
    "portfolio": 0,
    "zone": 1,
    "load": 1,
    "wx_zone": 2,
    "solar_zone": 2,
    "wind_zone": 2,
    "solar": 2,
    "wind": 2,
    "cdr_zone": 3,
}
_LOWEST_PRIORITY = 4


def _priority(loc: Dict[str, Any]) -> int:
    return _TYPE_PRIORITY.get((loc.get("resource_type") or "").lower(), _LOWEST_PRIORITY)


def _best_tier(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candidates:
        return []
    best = min(_priority(c) for c in candidates)
    return [c for c in candidates if _priority(c) == best]


def _match_resource(
    resources: List[Dict[str, Any]], raw: str
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Resolve one location name.

    Returns (match, ambiguous). Exact name/id hits beat partial ones, and the
    most aggregated resource type wins a tie. Anything still tied is reported
    as ambiguous rather than resolved to whichever row came back first.
    """
    needle = normalize(raw)
    if not needle:
        return None, []

    exact: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    for r in resources:
        names = [
            r.get("resource_name") or "",
            r.get("weather_sims_id") or "",
            r.get("energy_sims_id") or "",
        ]
        if any(normalize(n) == needle for n in names if n):
            exact.append(_as_location(r))
        elif any(phrase_overlap(needle, n) for n in names if n):
            partial.append(_as_location(r))

    for candidates in (exact, partial):
        tier = _best_tier(candidates)
        if len(tier) == 1:
            return tier[0], []
        if len(tier) > 1:
            return None, tier
    return None, []


def _location_examples(ctx: ResolverContext, limit: int = 2) -> str:
    """Examples taken from the entity in play — never another market's zones."""
    zone_names: List[str] = []
    for r in _catalog_resources(ctx):
        if (r.get("resource_type") or "").lower() not in ("zone", "load"):
            continue
        name = (r.get("resource_name") or "").strip()
        if name and name not in zone_names:
            zone_names.append(name)
        if len(zone_names) >= limit:
            break

    examples = zone_names + ["RTO"]
    if zone_names:
        examples.append("All Load Zones")
    return ", ".join(examples)


def _metadata_label(dim) -> str:
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    if values:
        return values[0]
    criteria = dim.criteria or {}
    type_filter = criteria.get("type_filter") or criteria.get("resource_types") or []
    if isinstance(type_filter, str):
        type_filter = [type_filter]
    normalized = [str(t).lower() for t in type_filter]
    if any("wx" in t or "weather" in t for t in normalized):
        return "Weather locations"
    if any(t == "load" or "load" in t for t in normalized):
        return "Load zones"
    if any("solar" in t for t in normalized):
        return "Solar zones"
    if any("wind" in t for t in normalized):
        return "Wind zones"
    return "Available locations"


def _needs_entity_for_metadata_locations(dim) -> bool:
    """True when the metadata ask is about locations/resources of an entity."""
    criteria = dim.criteria or {}
    if criteria.get("type_filter") or criteria.get("resource_types"):
        return True
    role = (dim.role or "").lower()
    mode = (dim.mode or "").lower()
    if mode == "metadata_query" and role in ("dimension", "filter", ""):
        # Default metadata location asks are entity-scoped
        return True
    return False


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = ctx.aep.query.intent
    dim = ctx.aep.query.location
    mode = (dim.mode or "explicit").lower()
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]

    if is_awareness(intent):
        ctx.locations = ResolvedLocations(
            mode="none",
            count=0,
            values=[],
            label="N/A",
        )
        return ctx

    # A catalog lookup needs no location. LLM1 leaves `location` at its default
    # `explicit` mode when the ask is about variables or initializations, so keying
    # only on the mode would demand a location the question never mentioned.
    if is_metadata(intent) and (mode in ("metadata_query", "none", "") or not values):
        if not ctx.entity and _needs_entity_for_metadata_locations(dim):
            allowed = ", ".join(
                str(e.get("entity") or e.get("shortname")) for e in ctx.allowed_entities
            ) or "(none)"
            ctx.errors.append(
                f"Which entity's locations should I list? You have access to: {allowed}."
            )
            ctx.unresolved.add("location")
            return ctx
        ctx.locations = ResolvedLocations(
            mode="metadata_query",
            count=0,
            values=[],
            label=_metadata_label(dim),
        )
        return ctx

    if not ctx.entity:
        if "entity" not in ctx.unresolved:
            ctx.errors.append(
                "I need an entity before I can resolve locations. "
                "Which entity should this apply to?"
            )
        ctx.unresolved.add("location")
        return ctx

    resources = _catalog_resources(ctx)

    if mode == "logical_group" or (
        values and values[0].strip().lower() in _LOGICAL_MAP
    ):
        label = values[0] if values else "logical group"
        key = label.strip().lower()
        resource_types = list(_LOGICAL_MAP.get(key) or ())
        if not resource_types:
            criteria = dim.criteria or {}
            rt = str(criteria.get("resource_type") or "").lower()
            if rt:
                resource_types = [rt]
        if not resource_types:
            ctx.errors.append(
                f"I don't recognize the location group '{label}'. "
                "Try All Load Zones, RTO, All Solar Zones, or All Wind Zones."
            )
            ctx.unresolved.add("location")
            return ctx
        resolved = _resources_by_types(resources, resource_types)
        if not resolved and "portfolio" in resource_types:
            portfolio = (ctx.entity_catalog.get(ctx.entity.name) or {}).get("portfolio")
            if portfolio:
                resolved = [
                    {
                        "location_name": "RTO",
                        "weather_sims_id": portfolio.get("weather_sims_id") or "",
                        "energy_sims_id": portfolio.get("energy_sims_id") or "",
                        "resource_type": "portfolio",
                    }
                ]
        if not resolved:
            available = sorted(
                {
                    (r.get("resource_type") or "").lower()
                    for r in resources
                    if r.get("resource_type")
                }
            )
            avail_txt = ", ".join(available) if available else "none in catalog"
            ctx.errors.append(
                f"I couldn't find locations for '{label}' on {ctx.entity.display_name}. "
                f"Available location types there: {avail_txt}."
            )
            ctx.unresolved.add("location")
            return ctx
        ctx.locations = ResolvedLocations(
            mode="logical_group",
            count=len(resolved),
            values=resolved,
            label=label,
        )
        return ctx

    if mode == "metadata_query":
        # Phase 1: treat as confirmation of a metadata discovery request
        ctx.locations = ResolvedLocations(
            mode="metadata_query",
            count=0,
            values=[],
            label=values[0] if values else "metadata lookup",
        )
        return ctx

    if not values:
        ctx.errors.append(
            "Which location (or location group) should we use? "
            f"Examples: {_location_examples(ctx)}."
        )
        ctx.unresolved.add("location")
        return ctx

    resolved_vals: List[Dict[str, Any]] = []
    for raw in values:
        key = raw.strip().lower()
        if key in _LOGICAL_MAP:
            group = _resources_by_types(resources, list(_LOGICAL_MAP[key]))
            if group:
                resolved_vals.extend(group)
                continue
        matched, ambiguous = _match_resource(resources, raw)
        if ambiguous:
            options = ", ".join(sorted({m["location_name"] for m in ambiguous}))
            ctx.errors.append(
                f"'{raw}' matches several {ctx.entity.display_name} locations "
                f"({options}). Which one did you mean?"
            )
            ctx.unresolved.add("location")
            return ctx
        if not matched:
            try:
                hit = metadata_db.resolve_location(ctx.entity.id, raw)
            except Exception:
                hit = None
            if hit:
                matched = {
                    "location_name": hit.get("location_name") or raw,
                    "weather_sims_id": hit.get("weather_sims_id") or "",
                    "energy_sims_id": hit.get("energy_sims_id") or "",
                    "resource_type": hit.get("resource_type") or "",
                    "is_aggregate": hit.get("is_aggregate"),
                }
        if not matched:
            ctx.errors.append(
                f"Location '{raw}' could not be resolved for {ctx.entity.display_name}."
            )
            ctx.unresolved.add("location")
            return ctx
        resolved_vals.append(matched)

    # De-dupe by energy/weather sims id
    seen = set()
    unique: List[Dict[str, Any]] = []
    for v in resolved_vals:
        k = (v.get("energy_sims_id"), v.get("weather_sims_id"), v.get("location_name"))
        if k in seen:
            continue
        seen.add(k)
        unique.append(v)

    label = ", ".join(v.get("location_name") or "?" for v in unique)
    ctx.locations = ResolvedLocations(
        mode="explicit",
        count=len(unique),
        values=unique,
        label=label,
    )
    return ctx
