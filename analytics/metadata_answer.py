"""Answer catalog (metadata) questions directly from the loaded catalog.

A metadata ask is a lookup, not an analytical plan: the catalog is already in
memory from the LLM1 injection, so there is nothing for the user to confirm and
no SQL to generate. These helpers turn the resolved plan into the answer itself.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from analytics.models import AnalyticalExecutionPlan, ResolvedExecutionPlan
from analytics.text_match import normalize, phrase_overlap, tokenize

# What the user actually asked to discover. LLM1 often flags several dimensions
# as `metadata_query` at once, so the wording is the tie-breaker.
_TARGET_KEYWORDS = {
    "variables": {
        "variable",
        "variables",
        "metric",
        "metrics",
        "field",
        "fields",
        "measure",
        "measures",
    },
    "locations": {
        "location",
        "locations",
        "zone",
        "zones",
        "site",
        "sites",
        "region",
        "regions",
        "node",
        "nodes",
        "place",
        "places",
    },
    "initializations": {
        "initialization",
        "initializations",
        "init",
        "inits",
        "run",
        "runs",
        "vintage",
        "vintages",
    },
    "entities": {
        "entity",
        "entities",
        "project",
        "projects",
        "iso",
        "isos",
        "market",
        "markets",
    },
}

_TYPE_LABELS = {
    "portfolio": "System",
    "zone": "Load Zones",
    "load": "Load Zones",
    "wx_zone": "Weather Zones",
    "solar_zone": "Solar Regions",
    "solar": "Solar Regions",
    "wind_zone": "Wind Regions",
    "wind": "Wind Regions",
    "cdr_zone": "CDR Zones",
}

_TYPE_ORDER = [
    "portfolio",
    "zone",
    "load",
    "wx_zone",
    "solar_zone",
    "solar",
    "wind_zone",
    "wind",
    "cdr_zone",
]

# Filter wording LLM1 may send → catalog resource types.
_FILTER_ALIASES = {
    "portfolio": ("portfolio",),
    "rto": ("portfolio",),
    "system": ("portfolio",),
    "zone": ("zone", "load"),
    "load": ("zone", "load"),
    "load_zone": ("zone", "load"),
    "wx_zone": ("wx_zone",),
    "weather": ("wx_zone",),
    "weather_zone": ("wx_zone",),
    "solar_zone": ("solar_zone", "solar"),
    "solar": ("solar_zone", "solar"),
    "wind_zone": ("wind_zone", "wind"),
    "wind": ("wind_zone", "wind"),
    "cdr_zone": ("cdr_zone",),
    "cdr": ("cdr_zone",),
}

_ENSEMBLE_LABELS = {
    "weather": "Weather",
    "energy": "Energy",
    "fundamental_market": "Market",
}


def _label_for_type(resource_type: str) -> str:
    rt = (resource_type or "").lower()
    return _TYPE_LABELS.get(rt, rt.replace("_", " ").title() or "Other")


def _type_sort_key(resource_type: str) -> int:
    rt = (resource_type or "").lower()
    return _TYPE_ORDER.index(rt) if rt in _TYPE_ORDER else len(_TYPE_ORDER)


def _wanted_types(criteria: Dict[str, Any]) -> set:
    raw = (criteria or {}).get("type_filter") or (criteria or {}).get("resource_types") or []
    if isinstance(raw, str):
        raw = [raw]
    wanted: set = set()
    for item in raw:
        key = str(item or "").strip().lower()
        wanted.update(_FILTER_ALIASES.get(key, (key,) if key else ()))
    return {w for w in wanted if w}


def _entity_label(rep: Optional[ResolvedExecutionPlan]) -> str:
    if rep and rep.entity and rep.entity.display_name:
        return rep.entity.display_name
    return ""


def _entity_key(rep: Optional[ResolvedExecutionPlan]) -> str:
    if rep and rep.entity and rep.entity.name:
        return rep.entity.name
    return ""


def _grouped_resources(
    resources: Sequence[Dict[str, Any]], wanted: set
) -> Dict[str, List[str]]:
    grouped: Dict[str, List[str]] = {}
    for r in resources:
        rt = (r.get("resource_type") or "").lower()
        if wanted and rt not in wanted:
            continue
        name = (r.get("resource_name") or "").strip()
        if not name:
            continue
        grouped.setdefault(rt, []).append(name)
    for names in grouped.values():
        names.sort()
    return grouped


def _render_groups(grouped: Dict[str, List[str]]) -> List[str]:
    lines: List[str] = []
    for rt in sorted(grouped, key=_type_sort_key):
        names = grouped[rt]
        lines.append("")
        lines.append(f"**{_label_for_type(rt)} ({len(names)})**")
        lines.extend(f"• {n}" for n in names)
    return lines


def answer_locations(
    aep: AnalyticalExecutionPlan,
    key: str,
    label: str,
    entity_catalog: Dict[str, Any],
) -> Optional[str]:
    if not key:
        return None
    bucket = entity_catalog.get(key) or {}
    resources = list(bucket.get("resources") or [])
    if not resources:
        return None

    wanted = _wanted_types(aep.query.location.criteria)
    grouped = _grouped_resources(resources, wanted)
    label = label or key

    if not grouped:
        scope = ", ".join(sorted(_label_for_type(w) for w in wanted)) or "that type"
        return f"{label} doesn't have any {scope} in the catalog."

    total = sum(len(v) for v in grouped.values())
    scope_txt = ""
    if wanted:
        scope_txt = " " + ", ".join(sorted({_label_for_type(w) for w in wanted}))
    head = f"{label} has {total}{scope_txt} location{'s' if total != 1 else ''} you can query:"
    return "\n".join([head] + _render_groups(grouped))


def _format_variable_line(
    name: str, by_name: Dict[str, Dict[str, Any]]
) -> str:
    entry = by_name.get(name) or {}
    display = str(entry.get("display_name") or name)
    unit = str(entry.get("unit") or "")
    line = f"• {display} (`{name}`)"
    if unit:
        line += f" — {unit}"
    return line


def answer_variables(
    key: str,
    label: str,
    entity_variables: Dict[str, Any],
    variable_catalog: Sequence[Dict[str, Any]],
) -> Optional[str]:
    if not key:
        return None
    avail = entity_variables.get(key) or {}
    names = [str(v) for v in (avail.get("variables") or []) if v]
    if not names:
        return None

    by_name = {str(e.get("variable")): e for e in variable_catalog or []}
    by_category: Dict[str, List[str]] = {}
    for name in sorted(names):
        entry = by_name.get(name) or {}
        category = str(entry.get("category") or "Other")
        by_category.setdefault(category, []).append(_format_variable_line(name, by_name))

    label = label or key
    lines = [f"{label} has {len(names)} variables available:"]
    for category in sorted(by_category):
        lines.append("")
        lines.append(f"**{category} ({len(by_category[category])})**")
        lines.extend(by_category[category])
    return "\n".join(lines)


def _variables_by_resource_type(avail: Dict[str, Any]) -> Dict[str, List[str]]:
    """Prefer the merged map; fall back to inverting energy_by_resource_type."""
    raw = avail.get("variables_by_resource_type") or {}
    if raw:
        return {str(rt): [str(v) for v in vars_] for rt, vars_ in raw.items()}

    inverted: Dict[str, List[str]] = {}
    for variable, rts in (avail.get("energy_by_resource_type") or {}).items():
        for rt in rts or []:
            inverted.setdefault(str(rt).lower(), []).append(str(variable))
    for rt, names in inverted.items():
        inverted[rt] = sorted(set(names))
    return inverted


def answer_variables_by_location(
    aep: AnalyticalExecutionPlan,
    key: str,
    label: str,
    entity_catalog: Dict[str, Any],
    entity_variables: Dict[str, Any],
    variable_catalog: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """Variables available for each location type (and the named places of that type)."""
    if not key:
        return None
    avail = entity_variables.get(key) or {}
    by_rt = _variables_by_resource_type(avail)
    resources = list((entity_catalog.get(key) or {}).get("resources") or [])
    if not by_rt and not resources:
        return None

    wanted = _wanted_types(aep.query.location.criteria)
    locations_by_rt = _grouped_resources(resources, wanted)
    # Union of types that have either named places or linked variables
    types = set(locations_by_rt) | set(by_rt)
    if wanted:
        types &= wanted
    if not types:
        return None

    by_name = {str(e.get("variable")): e for e in variable_catalog or []}
    label = label or key
    lines = [f"{label} — variables available per location type:"]

    # Merge sibling resource types that share a display label (zone/load,
    # solar/solar_zone, …) so the user sees one Load Zones section, not two.
    merged: Dict[str, Dict[str, Any]] = {}
    for rt in sorted(types, key=_type_sort_key):
        group_label = _label_for_type(rt)
        bucket = merged.setdefault(
            group_label,
            {"sort": _type_sort_key(rt), "locs": [], "vars": set()},
        )
        bucket["sort"] = min(bucket["sort"], _type_sort_key(rt))
        for n in locations_by_rt.get(rt) or []:
            if n not in bucket["locs"]:
                bucket["locs"].append(n)
        bucket["vars"].update(by_rt.get(rt) or [])

    for group_label, bucket in sorted(merged.items(), key=lambda kv: kv[1]["sort"]):
        loc_names = bucket["locs"]
        var_names = sorted(bucket["vars"])
        lines.append("")
        header = f"**{group_label}**"
        if loc_names:
            header += f" ({len(loc_names)}): {', '.join(loc_names)}"
        lines.append(header)
        if not var_names:
            lines.append("• (no variables linked for this type)")
            continue
        by_category: Dict[str, List[str]] = {}
        for name in var_names:
            entry = by_name.get(name) or {}
            category = str(entry.get("category") or "Other")
            by_category.setdefault(category, []).append(
                _format_variable_line(name, by_name)
            )
        for category in sorted(by_category):
            lines.append(f"*{category}*")
            lines.extend(by_category[category])
    return "\n".join(lines)


def answer_initializations(
    key: str,
    label: str,
    latest_inits: Dict[str, Any],
) -> Optional[str]:
    if not key:
        return None
    bucket = latest_inits.get(key) or {}
    rows: List[str] = []
    for etype in ("weather", "energy", "fundamental_market"):
        windows = bucket.get(etype) or {}
        if not windows:
            continue
        rows.append("")
        rows.append(f"**{_ENSEMBLE_LABELS.get(etype, etype.title())}**")
        for window in sorted(windows):
            rows.append(f"• {window.replace('_', ' ')} — {windows[window]}")
    if not rows:
        return None
    label = label or key
    return "\n".join([f"Latest initializations available for {label}:"] + rows)


def answer_entities(allowed_entities: Sequence[Dict[str, Any]]) -> Optional[str]:
    names = [
        str(e.get("entity") or e.get("shortname") or "").strip()
        for e in allowed_entities or []
    ]
    names = [n for n in names if n]
    if not names:
        return None
    lines = [f"You have access to {len(names)} entit{'ies' if len(names) != 1 else 'y'}:"]
    lines.extend(f"• {n}" for n in sorted(names))
    return "\n".join(lines)


def _keyword_target(message: str) -> Optional[str]:
    """Which catalog the wording asks for, or None when it is not decisive."""
    tokens = set(tokenize(message))
    if not tokens:
        return None
    scores = {
        target: len(tokens & keywords) for target, keywords in _TARGET_KEYWORDS.items()
    }
    best = max(scores.values())
    if not best:
        return None
    winners = [target for target, score in scores.items() if score == best]
    return winners[0] if len(winners) == 1 else None


def _flagged_targets(aep: AnalyticalExecutionPlan) -> List[str]:
    query = aep.query
    pairs = (
        ("locations", query.location),
        ("variables", query.variable),
        ("initializations", query.initialization),
        ("entities", query.entity),
    )
    return [
        name
        for name, dim in pairs
        if (getattr(dim, "mode", "") or "").lower() == "metadata_query"
    ]


_CROSS_LOCATION_PHRASES = (
    "per location",
    "per zone",
    "per region",
    "for each location",
    "for each zone",
    "by location",
    "by zone",
    "each location",
    "each zone",
    "variables per",
    "variable per",
)


def _is_variables_by_location(message: str, flagged: List[str]) -> bool:
    """True when the ask is the cross product, not a single catalog."""
    tokens = set(tokenize(message))
    has_var = bool(tokens & _TARGET_KEYWORDS["variables"])
    has_loc = bool(tokens & _TARGET_KEYWORDS["locations"])
    if not (has_var and has_loc):
        # LLM1 may flag both dimensions even when the wording is one-sided
        if "variables" in flagged and "locations" in flagged and has_var:
            lower = (message or "").lower()
            return any(p in lower for p in _CROSS_LOCATION_PHRASES) or "per" in tokens
        return False
    lower = (message or "").lower()
    if any(p in lower for p in _CROSS_LOCATION_PHRASES) or "per" in tokens:
        return True
    # Both catalogs named with no clear "list of locations" framing → cross
    return "variables" in flagged and "locations" in flagged


def _pick_target(aep: AnalyticalExecutionPlan, message: str) -> Optional[str]:
    flagged = _flagged_targets(aep)
    if _is_variables_by_location(message, flagged):
        return "variables_by_location"
    keyword = _keyword_target(message)
    # The wording wins whenever the plan left that door open. LLM1 routinely
    # flags `location` as boilerplate on every metadata plan, so trusting the
    # dimension order alone answers the wrong question.
    if keyword and (not flagged or keyword in flagged):
        return keyword
    if len(flagged) == 1:
        return flagged[0]
    # Both flagged with a tied keyword score: prefer variables when the wording
    # named them at all, otherwise locations.
    if "variables" in flagged and "locations" in flagged:
        tokens = set(tokenize(message))
        if tokens & _TARGET_KEYWORDS["variables"]:
            return "variables"
        return "locations"
    if "locations" in flagged:
        return "locations"
    return flagged[0] if flagged else None


def _entity_targets(
    aep: AnalyticalExecutionPlan,
    rep: Optional[ResolvedExecutionPlan],
    allowed_entities: Sequence[Dict[str, Any]],
) -> List[tuple]:
    """Every entity the user named, as (shortname, display name).

    The analytical resolver binds a single entity because a forecast runs against
    one market. A catalog question can legitimately name several ("MISO and PJM"),
    so match them all here rather than silently answering for the first.
    """
    values = [str(v).strip() for v in (aep.query.entity.values or []) if str(v).strip()]
    matched: List[tuple] = []
    seen = set()
    for raw in values:
        needle = normalize(raw)
        if not needle:
            continue
        hit = None
        for ent in allowed_entities or []:
            names = {normalize(ent.get("entity")), normalize(ent.get("shortname"))} - {""}
            if needle in names:
                hit = ent
                break
            if any(phrase_overlap(needle, n) for n in names):
                hit = hit or ent
        if not hit:
            continue
        key = str(hit.get("shortname") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        matched.append((key, str(hit.get("entity") or key)))

    if matched:
        return matched
    key = _entity_key(rep)
    return [(key, _entity_label(rep) or key)] if key else []


def answer(
    aep: AnalyticalExecutionPlan,
    rep: Optional[ResolvedExecutionPlan],
    *,
    message: str = "",
    allowed_entities: Sequence[Dict[str, Any]],
    entity_catalog: Dict[str, Any],
    entity_variables: Dict[str, Any],
    variable_catalog: Sequence[Dict[str, Any]],
    latest_inits: Dict[str, Any],
) -> Optional[str]:
    """Answer a resolved metadata plan, or None when it needs the normal path."""
    target = _pick_target(aep, message)
    if target == "entities" and not _entity_key(rep):
        return answer_entities(allowed_entities)

    if target not in (
        "variables",
        "variables_by_location",
        "initializations",
        "locations",
    ):
        if not (rep and rep.locations and rep.locations.mode == "metadata_query"):
            return None
        target = "locations"

    sections: List[str] = []
    empty: List[str] = []
    for key, label in _entity_targets(aep, rep, allowed_entities):
        if target == "variables_by_location":
            section = answer_variables_by_location(
                aep,
                key,
                label,
                entity_catalog,
                entity_variables,
                variable_catalog,
            )
        elif target == "variables":
            section = answer_variables(key, label, entity_variables, variable_catalog)
        elif target == "initializations":
            section = answer_initializations(key, label, latest_inits)
        else:
            section = answer_locations(aep, key, label, entity_catalog)
        if section:
            sections.append(section)
        else:
            empty.append(label)

    # Nothing at all to show: fall back to the normal path rather than assert
    # emptiness we cannot stand behind.
    if not sections:
        return None
    # Say so explicitly when one of several named entities had nothing, so a
    # multi-entity ask never looks like it quietly dropped one.
    if empty:
        sections.append(f"Nothing available in the catalog for {', '.join(empty)}.")
    return "\n\n".join(sections)
