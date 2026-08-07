"""Build LLM1 runtime injection catalogs from metadata + ACL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from data import metadata_db
from security.acl import UserACL

# Business aliases → canonical variable names (platform knowledge for LLM1).
VARIABLE_ALIASES: Dict[str, List[str]] = {
    "temp_2m": ["temperature", "temp", "air temperature", "surface temperature", "2m temperature"],
    "temp_100m": ["100m temperature", "temp 100m"],
    "wind_speed_100m": ["wind", "wind speed", "100m wind", "wind speed 100m"],
    "wind_speed_10m": ["10m wind", "surface wind"],
    "solar_radiation": ["solar", "irradiance", "ghi", "solar radiation"],
    "load": ["demand", "electricity demand", "power demand", "load"],
    "gsi": ["generation stress index", "stress index", "gsi"],
    "wind_gen": ["wind generation", "wind power", "wind gen"],
    "solar_gen": ["solar generation", "solar power", "solar gen", "pv generation"],
}

VARIABLE_CATEGORIES: Dict[str, str] = {
    "temp_2m": "Weather",
    "temp_100m": "Weather",
    "wind_speed_100m": "Weather",
    "wind_speed_10m": "Weather",
    "solar_radiation": "Weather",
    "load": "Energy",
    "gsi": "Energy",
    "wind_gen": "Energy",
    "solar_gen": "Energy",
}

VARIABLE_DISPLAY: Dict[str, str] = {
    "temp_2m": "2 m Air Temperature",
    "temp_100m": "100 m Air Temperature",
    "wind_speed_100m": "100 m Wind Speed",
    "wind_speed_10m": "10 m Wind Speed",
    "solar_radiation": "Solar Radiation",
    "load": "Electric Load",
    "gsi": "Generation Stress Index",
    "wind_gen": "Wind Generation",
    "solar_gen": "Solar Generation",
}

LOGICAL_LOCATION_GROUPS = [
    {
        "name": "RTO",
        "aliases": ["rto", "iso", "system", "whole system"],
        "description": "Single system-level aggregate for the entity",
    },
    {
        "name": "All Load Zones",
        "aliases": ["all load zones", "load zones", "every load zone"],
        "description": "All aggregate load-zone resources for the entity",
    },
    {
        "name": "All Solar Zones",
        "aliases": ["all solar zones", "solar zones"],
        "description": "All solar_zone resources for the entity",
    },
    {
        "name": "All Wind Zones",
        "aliases": ["all wind zones", "wind zones"],
        "description": "All wind_zone resources for the entity",
    },
]


def _all_entity_ids() -> List[str]:
    try:
        with metadata_db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT entity_id FROM entities")
                return [str(r[0]) for r in cur.fetchall()]
    except Exception:
        return []


def build_variable_catalog(units: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    units = units if units is not None else metadata_db.get_variable_units()
    catalog: List[Dict[str, Any]] = []
    seen = set()
    # Prefer known analytical variables first
    for name, aliases in VARIABLE_ALIASES.items():
        seen.add(name)
        catalog.append(
            {
                "variable": name,
                "display_name": VARIABLE_DISPLAY.get(name, name),
                "aliases": aliases,
                "category": VARIABLE_CATEGORIES.get(name, "Other"),
                "unit": units.get(name) or "",
            }
        )
    # Include remaining DB variables without inventing aliases
    for name, unit in sorted(units.items()):
        if name in seen:
            continue
        catalog.append(
            {
                "variable": name,
                "display_name": name,
                "aliases": [],
                "category": VARIABLE_CATEGORIES.get(name, "Other"),
                "unit": unit or "",
            }
        )
    return catalog


def resolve_variable_name(raw: str, catalog: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    needle = (raw or "").strip().lower().replace(" ", "_")
    if not needle:
        return None
    catalog = catalog or build_variable_catalog()
    for entry in catalog:
        candidates = [entry["variable"].lower()]
        candidates.extend(a.lower().replace(" ", "_") for a in entry.get("aliases") or [])
        candidates.append(entry.get("display_name", "").lower().replace(" ", "_"))
        if needle in candidates or needle.replace("_", "") in {c.replace("_", "") for c in candidates}:
            return entry
        # Loose contains for phrases like "temperature"
        plain = (raw or "").strip().lower()
        for alias in entry.get("aliases") or []:
            if plain == alias.lower() or plain in alias.lower() or alias.lower() in plain:
                return entry
    return None


def build_llm1_injection(user: dict, acl: UserACL) -> Dict[str, Any]:
    entity_ids = acl.entity_ids if not acl.is_admin else _all_entity_ids()
    allowed_entities = metadata_db.load_allowed_entities(entity_ids) if entity_ids else []
    shortnames = [e["shortname"] for e in allowed_entities if e.get("shortname")]
    latest_inits = metadata_db.get_latest_inits_nested(shortnames) if shortnames else {}
    catalog_entity_ids = [str(e["entity_id"]) for e in allowed_entities if e.get("entity_id")]
    entity_catalog = (
        metadata_db.load_entity_catalog(catalog_entity_ids) if catalog_entity_ids else {}
    )
    variable_catalog = build_variable_catalog()

    location_types: Dict[str, Any] = {}
    for ent in allowed_entities:
        sn = ent.get("shortname")
        if not sn:
            continue
        bucket = entity_catalog.get(sn) or {}
        resources = bucket.get("resources") or []
        type_counts: Dict[str, int] = {}
        named_by_type: Dict[str, List[str]] = {}
        for r in resources:
            rt = (r.get("resource_type") or "unknown").lower()
            type_counts[rt] = type_counts.get(rt, 0) + 1
            if rt in ("load", "solar_zone", "wind_zone", "wx_zone", "portfolio"):
                named_by_type.setdefault(rt, []).append(r.get("resource_name") or "")
        location_types[sn] = {
            "counts_by_type": type_counts,
            "examples": {k: v[:8] for k, v in named_by_type.items()},
            "logical_groups": LOGICAL_LOCATION_GROUPS,
        }

    return {
        "username": user.get("metadata_username") or user.get("email") or "",
        "current_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "allowed_entities": [
            {
                "entity": e.get("entity"),
                "shortname": e.get("shortname"),
                "timezone": e.get("timezone"),
                "type": "ISO",
            }
            for e in allowed_entities
        ],
        "variable_catalog": variable_catalog,
        "location_types": location_types,
        "logical_location_groups": LOGICAL_LOCATION_GROUPS,
        "latest_inits_available": {
            sn: {
                etype: list(windows.keys())
                for etype, windows in (bucket or {}).items()
                if windows
            }
            for sn, bucket in latest_inits.items()
        },
        # Concrete inits are for the Resolver, not LLM1 — keep only availability signal above.
        "_resolver": {
            "allowed_entities": allowed_entities,
            "latest_inits": latest_inits,
            "entity_catalog": entity_catalog,
            "variable_catalog": variable_catalog,
        },
    }
