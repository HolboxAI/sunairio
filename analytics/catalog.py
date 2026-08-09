"""Build LLM1 runtime injection catalogs from metadata + ACL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from analytics.text_match import contains_phrase, tokenize
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
    "gsi": "Grid Stress Index",
    "wind_gen": "Wind Generation",
    "solar_gen": "Solar Generation",
}

LOGICAL_LOCATION_GROUPS = [
    {
        "name": "RTO",
        "aliases": ["rto", "iso", "system", "whole system"],
        "description": "Single system-level aggregate for the entity",
        "maps_to_resource_types": ["portfolio"],
    },
    {
        "name": "All Load Zones",
        "aliases": ["all load zones", "load zones", "every load zone"],
        "description": "All aggregate load zones for the entity (resource_type zone / load)",
        "maps_to_resource_types": ["zone", "load"],
    },
    {
        "name": "All Solar Zones",
        "aliases": ["all solar zones", "solar zones"],
        "description": "All solar zone aggregates (resource_type solar_zone / solar)",
        "maps_to_resource_types": ["solar_zone", "solar"],
    },
    {
        "name": "All Wind Zones",
        "aliases": ["all wind zones", "wind zones"],
        "description": "All wind zone aggregates (resource_type wind_zone / wind)",
        "maps_to_resource_types": ["wind_zone", "wind"],
    },
    {
        "name": "All Weather Zones",
        "aliases": ["all weather zones", "weather zones"],
        "description": "All weather zone aggregates (resource_type wx_zone)",
        "maps_to_resource_types": ["wx_zone"],
    },
]

# RTO stays offered for every entity: the location stage falls back to the entity's
# portfolio record even when no portfolio row appears in its resource list.
_ALWAYS_AVAILABLE_GROUPS = {"RTO"}


def groups_for_resource_types(type_counts: Dict[str, int]) -> List[Dict[str, Any]]:
    """Logical groups that actually expand to resources for one entity.

    The group vocabulary is platform-wide, but availability is not: an entity with
    only load zones must not be told it has wind or solar zones, or LLM1 will offer
    groups that resolve to nothing.
    """
    present = {rt for rt, count in (type_counts or {}).items() if count}
    return [
        group
        for group in LOGICAL_LOCATION_GROUPS
        if group["name"] in _ALWAYS_AVAILABLE_GROUPS
        or present.intersection(group["maps_to_resource_types"])
    ]


def _all_entity_ids() -> List[str]:
    try:
        with metadata_db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT entity_id FROM entities")
                return [str(r[0]) for r in cur.fetchall()]
    except Exception:
        return []


# `variables.variable_type` is the platform's own classification; map it onto the
# labels users see. Anything unknown keeps the hardcoded fallback below.
_VARIABLE_TYPE_LABELS = {
    "weather": "Weather",
    "energy": "Energy",
    "fundamental_market": "Market",
    "market": "Market",
}


def _category_for(name: str, meta: Dict[str, Dict[str, str]]) -> str:
    """Category from the catalog's own `variable_type`, not a curated guess."""
    raw = str((meta.get(name) or {}).get("variable_type") or "").strip().lower()
    if raw:
        return _VARIABLE_TYPE_LABELS.get(raw, raw.replace("_", " ").title())
    return VARIABLE_CATEGORIES.get(name, "Other")


def _display_for(name: str, meta: Dict[str, Dict[str, str]]) -> str:
    # Curated names read better for the handful we have; the catalog's own
    # `variable_name` beats echoing the raw code for everything else.
    if name in VARIABLE_DISPLAY:
        return VARIABLE_DISPLAY[name]
    return str((meta.get(name) or {}).get("variable_name") or "") or name


def build_variable_catalog(
    units: Optional[Dict[str, str]] = None,
    *,
    meta: Optional[Dict[str, Dict[str, str]]] = None,
    allowed_names: Optional[set] = None,
) -> List[Dict[str, Any]]:
    units = units if units is not None else metadata_db.get_variable_units()
    meta = meta if meta is not None else metadata_db.get_variable_meta()
    catalog: List[Dict[str, Any]] = []
    seen = set()
    # Prefer known analytical variables first
    for name, aliases in VARIABLE_ALIASES.items():
        if allowed_names is not None and name not in allowed_names:
            continue
        seen.add(name)
        catalog.append(
            {
                "variable": name,
                "display_name": _display_for(name, meta),
                "aliases": aliases,
                "category": _category_for(name, meta),
                "unit": units.get(name) or "",
            }
        )
    # Include remaining DB variables without inventing aliases
    for name, unit in sorted(units.items()):
        if name in seen:
            continue
        if allowed_names is not None and name not in allowed_names:
            continue
        catalog.append(
            {
                "variable": name,
                "display_name": _display_for(name, meta),
                "aliases": [],
                "category": _category_for(name, meta),
                "unit": unit or "",
            }
        )
    return catalog


def filter_variable_catalog(
    catalog: List[Dict[str, Any]], allowed_names: set
) -> List[Dict[str, Any]]:
    """Keep catalog entries whose canonical name is in allowed_names."""
    if not allowed_names:
        return []
    return [e for e in catalog if (e.get("variable") or "") in allowed_names]


def _union_entity_variable_names(entity_variables: Dict[str, Dict[str, Any]]) -> set:
    names: set = set()
    for bucket in (entity_variables or {}).values():
        for name in bucket.get("variables") or []:
            if name:
                names.add(str(name))
    return names


# Fields LLM1 sees. The alias table stays resolver-side: mapping a user's phrasing
# onto a canonical name is what LLM1 is for, and alias arrays that collide with real
# variable names (e.g. "ghi" listed under solar_radiation while a `ghi` variable
# exists) only mislead it.
_LLM1_VARIABLE_FIELDS = ("variable", "display_name", "category", "unit")


def public_variable_catalog(catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Variable catalog view for LLM1 — canonical facts, no alias table."""
    return [{key: entry.get(key, "") for key in _LLM1_VARIABLE_FIELDS} for entry in catalog]


def _match_terms(entry: Dict[str, Any]) -> List[tuple]:
    """(term, specificity) pairs; canonical names outrank display names outrank aliases."""
    terms = [(entry.get("variable") or "", 3), (entry.get("display_name") or "", 2)]
    terms.extend((alias, 1) for alias in entry.get("aliases") or [])
    return terms


def resolve_variable_name(raw: str, catalog: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Best-scoring catalog entry for a business phrase.

    Scored rather than first-match so that a long precise alias ("wind generation")
    always beats a short generic one ("wind") from a different variable.
    """
    needle = tokenize(raw)
    if not needle:
        return None
    catalog = catalog or build_variable_catalog()

    best: Optional[Dict[str, Any]] = None
    best_score = 0
    for entry in catalog:
        for term, specificity in _match_terms(entry):
            tokens = tokenize(term)
            if not tokens:
                continue
            if tokens == needle:
                score = 1000 + specificity
            elif contains_phrase(needle, tokens):
                score = 100 + specificity + len(tokens)
            elif contains_phrase(tokens, needle):
                score = 50 + specificity + len(needle)
            else:
                continue
            if score > best_score:
                best_score = score
                best = entry
    return best


def build_llm1_injection(user: dict, acl: UserACL) -> Dict[str, Any]:
    entity_ids = acl.entity_ids if not acl.is_admin else _all_entity_ids()
    allowed_entities = metadata_db.load_allowed_entities(entity_ids) if entity_ids else []
    shortnames = [e["shortname"] for e in allowed_entities if e.get("shortname")]
    latest_inits = metadata_db.get_latest_inits_nested(shortnames) if shortnames else {}
    catalog_entity_ids = [str(e["entity_id"]) for e in allowed_entities if e.get("entity_id")]
    entity_catalog = (
        metadata_db.load_entity_catalog(catalog_entity_ids) if catalog_entity_ids else {}
    )
    entity_variables = (
        metadata_db.load_entity_variables(catalog_entity_ids) if catalog_entity_ids else {}
    )
    allowed_var_names = _union_entity_variable_names(entity_variables)
    # Scope the catalog to vars actually linked to the user's entities. When no
    # entities are linked yet, keep the empty catalog rather than the global list.
    variable_catalog = build_variable_catalog(
        allowed_names=allowed_var_names if catalog_entity_ids else set()
    )

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
            if rt in ("load", "zone", "solar_zone", "solar", "wind_zone", "wind", "wx_zone", "cdr_zone", "portfolio"):
                named_by_type.setdefault(rt, []).append(r.get("resource_name") or "")
        location_types[sn] = {
            "counts_by_type": type_counts,
            "examples": {k: v[:8] for k, v in named_by_type.items()},
            "logical_groups": groups_for_resource_types(type_counts),
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
        "variable_catalog": public_variable_catalog(variable_catalog),
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
            "entity_variables": entity_variables,
        },
    }
