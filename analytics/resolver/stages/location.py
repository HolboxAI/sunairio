"""LocationResolver — expand logical groups / resolve explicit names."""

from __future__ import annotations

from typing import Any, Dict, List

from analytics.models import ResolvedLocations, ResolverContext
from data import metadata_db

_LOGICAL_MAP = {
    "all load zones": "load",
    "load zones": "load",
    "all solar zones": "solar_zone",
    "solar zones": "solar_zone",
    "all wind zones": "wind_zone",
    "wind zones": "wind_zone",
    "rto": "portfolio",
    "iso": "portfolio",
    "system": "portfolio",
}


def _catalog_resources(ctx: ResolverContext) -> List[Dict[str, Any]]:
    if not ctx.entity:
        return []
    bucket = ctx.entity_catalog.get(ctx.entity.name) or {}
    return list(bucket.get("resources") or [])


def _resources_by_type(resources: List[Dict[str, Any]], resource_type: str) -> List[Dict[str, Any]]:
    rt = resource_type.lower()
    out = []
    for r in resources:
        if (r.get("resource_type") or "").lower() == rt:
            out.append(
                {
                    "location_name": r.get("resource_name") or "",
                    "weather_sims_id": r.get("weather_sims_id") or "",
                    "energy_sims_id": r.get("energy_sims_id") or "",
                    "resource_type": r.get("resource_type") or "",
                }
            )
    return out


def resolve(ctx: ResolverContext) -> ResolverContext:
    if not ctx.entity:
        ctx.errors.append("Cannot resolve locations without an entity.")
        return ctx

    dim = ctx.aep.query.location
    mode = (dim.mode or "explicit").lower()
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    resources = _catalog_resources(ctx)

    if mode == "logical_group" or (
        values and values[0].strip().lower() in _LOGICAL_MAP
    ):
        label = values[0] if values else "logical group"
        key = label.strip().lower()
        resource_type = _LOGICAL_MAP.get(key)
        if not resource_type:
            # Try criteria
            criteria = dim.criteria or {}
            resource_type = str(criteria.get("resource_type") or "").lower() or None
        if not resource_type:
            ctx.errors.append(f"Unknown logical location group '{label}'.")
            return ctx
        resolved = _resources_by_type(resources, resource_type)
        if not resolved and resource_type == "portfolio":
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
            ctx.errors.append(
                f"No locations found for logical group '{label}' on {ctx.entity.display_name}."
            )
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
        ctx.errors.append("Location is required.")
        return ctx

    resolved_vals: List[Dict[str, Any]] = []
    for raw in values:
        key = raw.strip().lower()
        if key in _LOGICAL_MAP:
            group = _resources_by_type(resources, _LOGICAL_MAP[key])
            if group:
                resolved_vals.extend(group)
                continue
        # Match catalog resources by name / sims id
        matched = None
        for r in resources:
            candidates = [
                (r.get("resource_name") or "").lower(),
                (r.get("weather_sims_id") or "").lower(),
                (r.get("energy_sims_id") or "").lower(),
            ]
            if key in candidates or any(key in c for c in candidates if c):
                matched = {
                    "location_name": r.get("resource_name") or "",
                    "weather_sims_id": r.get("weather_sims_id") or "",
                    "energy_sims_id": r.get("energy_sims_id") or "",
                    "resource_type": r.get("resource_type") or "",
                }
                break
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
                    "resource_type": "",
                }
        if not matched:
            ctx.errors.append(
                f"Location '{raw}' could not be resolved for {ctx.entity.display_name}."
            )
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
