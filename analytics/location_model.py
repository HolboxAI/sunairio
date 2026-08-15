"""Platform location granularity: aggregate zones vs point sites vs weights.

LLM1 never sees the full station list. This module is the shared vocabulary for
the consultant prompt injection, metadata answers, and (later) resolver/LLM2
lookups against `locations` + `location_weights`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Injected into LLM1 as `location_model`. Counts stay in `location_types`.
LOCATION_MODEL: Dict[str, Any] = {
    "is_aggregate": {
        "true": "Named zone or system portfolio. Weather is typically a weighted mix of point sites.",
        "false": "Point site (city, plant-area, weather station). May be a child of one or more aggregates.",
    },
    "attachment": {
        "weather": "Published on locations (aggregate and point).",
        "energy": "Published on resources (typically aggregate zones and the portfolio).",
    },
    "tables": {
        "locations": "Geography; `is_aggregate` lives here.",
        "resources": "Energy unit; FK to a location and a resource type.",
        "location_weights": "Parent aggregate, child point, variable, weight.",
        "location_variables": "Weather variables on a location.",
        "resource_variables": "Energy variables on a resource.",
    },
    "list_default": "aggregate",
    "criteria": {
        "granularity": "aggregate | point | both",
        "domain": "weather | energy",
        "composition": "true when asking which point sites form a named aggregate",
    },
}

# Wording that means point sites, not the named-zone catalog.
_POINT_PHRASES = (
    "weather station",
    "weather stations",
    "point location",
    "point locations",
    "point site",
    "point sites",
    "individual location",
    "individual locations",
    "independent weather",
    "independent location",
    "independent locations",
)
_BOTH_PHRASES = (
    "including stations",
    "including sites",
    "zones and stations",
    "stations and zones",
    "aggregate and point",
    "point and aggregate",
    "every location",
    "all locations including",
)

_COMPOSITION_PHRASES = (
    "made of",
    "made up",
    "make up",
    "composed of",
    "constituent",
    "constituents",
    "children of",
    "child stations",
    "which stations make",
    "what stations make",
    "stations that make",
    "weighted from",
    "weighting table",
    "location_weights",
    "parent zone",
)


def _lower(message: str) -> str:
    return (message or "").strip().lower()


def infer_granularity(
    message: str,
    criteria: Optional[Dict[str, Any]] = None,
    prior_granularity: Optional[str] = None,
) -> str:
    """aggregate (default) | point | both.

    Explicit `location.criteria.granularity` wins. Otherwise the user wording.
    Bare 'locations' / 'zones' stays aggregate — that is the named catalog.
    A follow-up that only narrows domain ("only the weather ones") keeps a
    prior point-site list instead of jumping back to aggregate zones.
    """
    raw = str((criteria or {}).get("granularity") or "").strip().lower()
    if raw in ("aggregate", "point", "both"):
        return raw
    text = _lower(message)
    if any(p in text for p in _BOTH_PHRASES):
        return "both"
    if any(p in text for p in _POINT_PHRASES):
        return "point"
    tokens = set(text.replace(",", " ").split())
    # 'plant' alone is too noisy (power plant questions that mean a zone).
    noisy = tokens & ({"station", "stations", "city", "cities", "farm", "farms"})
    if noisy and not any(z in tokens for z in ("zone", "zones", "region", "regions")):
        return "point"
    if "independent" in tokens and ("weather" in tokens or "location" in tokens or "locations" in tokens):
        return "point"
    if (prior_granularity or "") == "point":
        if tokens & {"these", "those", "ones", "them", "weather", "energy", "only"}:
            if not (tokens & {"zone", "zones", "region", "regions"}):
                return "point"
    return "aggregate"


def infer_domain(message: str, criteria: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """weather | energy | None (unspecified / both).

    Point-site 'weather' means locations with weather simulations — not
    resource_type wx_zone (those are aggregate weather *zones*).
    """
    crit = criteria or {}
    raw = str(crit.get("domain") or crit.get("ensemble_type") or "").strip().lower()
    if raw in ("weather", "energy"):
        return raw
    text = _lower(message)
    weather_ask = (
        "weather" in text
        or "only the weather" in text
        or "weather ones" in text
        or "weather one" in text
    )
    energy_ask = any(
        p in text
        for p in ("energy", "generation", "load mw", "solar gen", "wind gen")
    )
    if weather_ask and not energy_ask:
        return "weather"
    if energy_ask and not weather_ask:
        return "energy"
    return None


def infer_composition(message: str, criteria: Optional[Dict[str, Any]] = None) -> bool:
    crit = criteria or {}
    if crit.get("composition") in (True, "true", "1", 1):
        return True
    text = _lower(message)
    return any(p in text for p in _COMPOSITION_PHRASES)


def infer_scope(
    message: str,
    criteria: Optional[Dict[str, Any]] = None,
    prior_granularity: Optional[str] = None,
) -> Tuple[str, bool]:
    return infer_granularity(message, criteria, prior_granularity), infer_composition(message, criteria)
