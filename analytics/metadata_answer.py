"""Answer catalog (metadata) questions directly from the loaded catalog.

A metadata ask is a lookup, not an analytical plan: the catalog is already in
memory from the LLM1 injection, so there is nothing for the user to confirm and
no SQL to generate. These helpers turn the resolved plan into the answer itself.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from analytics.models import AnalyticalExecutionPlan, ResolvedExecutionPlan
from analytics.location_model import infer_domain, infer_scope
from analytics.text_match import normalize, phrase_overlap, tokenize
from data import metadata_db

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

# Point sites use resource_type as a *siting* tag (city vs farm), not zone family.
_POINT_TYPE_LABELS = {
    "load": "City / population weather",
    "zone": "City / population weather",
    "solar": "Solar-farm weather sites",
    "wind": "Wind-farm weather sites",
    "wx_zone": "Weather point sites",
}

_POINT_WEATHER_TYPES = {"load", "zone", "solar", "wind", "wx_zone"}
_POINT_ENERGY_TYPES = {"solar", "wind"}

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


def _label_for_type(resource_type: str, *, point: bool = False) -> str:
    rt = (resource_type or "").lower()
    if point:
        return _POINT_TYPE_LABELS.get(rt, _TYPE_LABELS.get(rt, rt.replace("_", " ").title() or "Other"))
    return _TYPE_LABELS.get(rt, rt.replace("_", " ").title() or "Other")


def _type_sort_key(resource_type: str) -> int:
    rt = (resource_type or "").lower()
    return _TYPE_ORDER.index(rt) if rt in _TYPE_ORDER else len(_TYPE_ORDER)


def _wanted_types(criteria: Dict[str, Any], *, point: bool = False, domain: Optional[str] = None) -> set:
    raw = (criteria or {}).get("type_filter") or (criteria or {}).get("resource_types") or []
    if isinstance(raw, str):
        raw = [raw]
    wanted: set = set()
    for item in raw:
        key = str(item or "").strip().lower()
        if point and key in ("weather", "weather_zone", "wx_zone"):
            # wx_zone is an aggregate partition. Weather *points* are cities + farm sites.
            wanted.update(_POINT_WEATHER_TYPES)
            continue
        if point and key in ("energy",):
            wanted.update(_POINT_ENERGY_TYPES)
            continue
        wanted.update(_FILTER_ALIASES.get(key, (key,) if key else ()))
    if point and domain == "weather":
        if not wanted or wanted <= {"wx_zone"} or wanted == _POINT_WEATHER_TYPES:
            return set(_POINT_WEATHER_TYPES)
    if point and domain == "energy":
        if not wanted or wanted <= {"wx_zone"}:
            return set(_POINT_ENERGY_TYPES)
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


def _render_groups(grouped: Dict[str, List[str]], *, point: bool = False) -> List[str]:
    lines: List[str] = []
    for rt in sorted(grouped, key=_type_sort_key):
        names = grouped[rt]
        lines.append("")
        lines.append(f"**{_label_for_type(rt, point=point)} ({len(names)})**")
        lines.extend(f"• {n}" for n in names)
    return lines


def _aggregation_blurb(key: str, location_types: Dict[str, Any]) -> str:
    stats = ((location_types.get(key) or {}).get("aggregation") or {})
    n_point = stats.get("point_locations")
    n_parents = stats.get("weighted_parents")
    n_children = stats.get("weighted_children")
    bits = [
        "These are **aggregate** named zones (`locations.is_aggregate = true`) "
        "plus the system portfolio — parent geographies, not a mix of extra "
        "variable types."
    ]
    if n_parents or n_children:
        bits.append(
            f"When a weight recipe exists, zone weather is a mix of point "
            f"stations in `location_weights` ({n_parents or 0} parents, "
            f"{n_children or 0} children for this entity)."
        )
    else:
        bits.append(
            "Zone weather is often a weighted mix of point stations stored in "
            "`location_weights` (parent = zone, child = city/station)."
        )
    if n_point:
        bits.append(
            f"This entity also has **{n_point} point sites** (cities, plant "
            "areas, weather stations; `is_aggregate = false`). Ask for "
            "stations/cities, or what makes up a named zone, to list those."
        )
    else:
        bits.append(
            "Point sites (cities, stations) also exist; ask for weather "
            "stations or what makes up a named zone to list them."
        )
    return " ".join(bits)


def _entity_ids_for(
    keys: Sequence[str], allowed_entities: Sequence[Dict[str, Any]]
) -> List[str]:
    wanted = {k for k in keys if k}
    ids: List[str] = []
    for ent in allowed_entities or []:
        if str(ent.get("shortname") or "") in wanted and ent.get("entity_id"):
            ids.append(str(ent["entity_id"]))
    return ids


def _safe_point_resources(
    key: str, allowed_entities: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    ids = _entity_ids_for([key], allowed_entities)
    if not ids:
        return []
    try:
        by_sn = metadata_db.load_entity_point_resources(ids)
    except Exception:
        return []
    return list(by_sn.get(key) or [])


def _safe_composition(key: str, parent_needles: Sequence[str]) -> List[Dict[str, Any]]:
    try:
        return metadata_db.load_location_composition(key, list(parent_needles))
    except Exception:
        return []


def _safe_locations_for_variables(
    key: str, variables: Sequence[str]
) -> List[Dict[str, Any]]:
    names = [str(v).strip() for v in variables if str(v).strip()]
    if not key or not names:
        return []
    try:
        return metadata_db.load_variables_for_locations(key, variables=names)
    except Exception:
        return []


_VAR_MATCH_SKIP = {
    "the",
    "and",
    "for",
    "all",
    "can",
    "you",
    "tell",
    "which",
    "from",
    "with",
    "have",
    "has",
    "that",
    "this",
    "what",
    "where",
    "when",
    "how",
}


def _expand_variable_family(
    names: Sequence[str], variable_catalog: Sequence[Dict[str, Any]]
) -> List[str]:
    catalog_names = {
        str(e.get("variable") or "").strip()
        for e in variable_catalog or []
        if e.get("variable")
    }
    out: List[str] = []
    seen = set()
    for raw in names:
        n = str(raw).strip()
        if not n:
            continue
        candidates = [n]
        if f"{n}_gen" in catalog_names:
            candidates.append(f"{n}_gen")
        if n.endswith("_gen") and n[:-4] in catalog_names:
            candidates.append(n[:-4])
        for c in candidates:
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
    return out


def _wanted_variables(
    aep: AnalyticalExecutionPlan,
    message: str,
    variable_catalog: Sequence[Dict[str, Any]],
) -> List[str]:
    """Catalog variable names the user used as a place filter, if any."""
    explicit = [
        str(v).strip()
        for v in (aep.query.variable.values or [])
        if str(v).strip() and (aep.query.variable.mode or "").lower() != "metadata_query"
    ]
    # metadata_query on variable with values still names the quantity
    if not explicit:
        explicit = [
            str(v).strip()
            for v in (aep.query.variable.values or [])
            if str(v).strip()
        ]
    catalog = list(variable_catalog or [])
    if explicit:
        return _expand_variable_family(explicit, catalog)

    if not catalog or not (message or "").strip():
        return []
    matched: List[str] = []
    seen = set()
    for entry in catalog:
        name = str(entry.get("variable") or "").strip()
        display = str(entry.get("display_name") or "").strip()
        if not name or name.lower() in _VAR_MATCH_SKIP:
            continue
        if phrase_overlap(name, message) or (display and phrase_overlap(display, message)):
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            matched.append(name)
    return _expand_variable_family(matched, catalog)


def _render_composition(rows: List[Dict[str, Any]]) -> List[str]:
    by_parent: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        parent = row.get("parent_name") or "Unknown zone"
        bucket = by_parent.setdefault(
            parent, {"children": {}, "sims": row.get("parent_weather_sims_id") or ""}
        )
        child = row.get("child_name") or ""
        if not child:
            continue
        info = bucket["children"].setdefault(
            child,
            {
                "sims": row.get("child_weather_sims_id") or "",
                "weights": [],
                "dynamic": False,
            },
        )
        w = row.get("weight")
        var = row.get("output_variable") or ""
        if w is not None and var:
            info["weights"].append(f"{var}={w:g}")
        if row.get("is_dynamic"):
            info["dynamic"] = True
    lines: List[str] = []
    for parent in sorted(by_parent):
        bucket = by_parent[parent]
        sims = bucket["sims"]
        header = f"**{parent}**"
        if sims:
            header += f" (`{sims}`)"
        header += " — aggregate parent; point children:"
        lines.append("")
        lines.append(header)
        for child in sorted(bucket["children"]):
            info = bucket["children"][child]
            extra = []
            if info["weights"]:
                extra.append(", ".join(info["weights"][:6]))
            if info["dynamic"]:
                extra.append("dynamic weights")
            suffix = f" — {'; '.join(extra)}" if extra else ""
            lines.append(f"• {child}{suffix}")
    return lines


def answer_locations(
    aep: AnalyticalExecutionPlan,
    key: str,
    label: str,
    entity_catalog: Dict[str, Any],
    *,
    message: str = "",
    allowed_entities: Sequence[Dict[str, Any]] = (),
    location_types: Optional[Dict[str, Any]] = None,
    out_list: Optional[List[Dict[str, Any]]] = None,
    catalog_locations: Optional[Dict[str, Any]] = None,
    variable_catalog: Sequence[Dict[str, Any]] = (),
) -> Optional[str]:
    if not key:
        return None
    bucket = entity_catalog.get(key) or {}
    resources = list(bucket.get("resources") or [])
    criteria = aep.query.location.criteria
    var_filter = _wanted_variables(aep, message, variable_catalog)
    if not resources and not var_filter:
        return None

    granularity, want_composition = infer_scope(
        message, criteria, (catalog_locations or {}).get("granularity")
    )
    domain = infer_domain(message, criteria)
    agg_wanted = _wanted_types(criteria)
    point_wanted = _wanted_types(criteria, point=True, domain=domain)
    grouped = _grouped_resources(resources, agg_wanted)
    label = label or key
    location_types = location_types or {}
    listed_names: List[str] = []

    parent_needles = [
        str(v).strip() for v in (aep.query.location.values or []) if str(v).strip()
    ]
    if want_composition:
        rows = _safe_composition(key, parent_needles)
        if rows:
            head = (
                f"{label} zone composition from `location_weights` "
                "(parent aggregate ← weighted point children):"
            )
            if parent_needles:
                head = (
                    f"{label} — stations that weight into "
                    f"{', '.join(parent_needles)}:"
                )
            listed_names = sorted({str(r.get("child_name") or "") for r in rows if r.get("child_name")})
            _record_listed(
                out_list,
                key=key,
                label=label,
                granularity="point",
                domain="weather",
                names=listed_names,
            )
            return "\n".join([head] + _render_composition(rows))
        if parent_needles:
            return (
                f"I don't have a `location_weights` recipe for "
                f"{', '.join(parent_needles)} on {label}. The named catalog "
                "still lists that place as an aggregate zone if it appears below."
            )
        # No named parent: explain and list aggregates so they can pick one.
        if grouped:
            total = sum(len(v) for v in grouped.values())
            head = (
                f"{label} has {total} aggregate location"
                f"{'s' if total != 1 else ''} whose stations I can expand. "
                "Name a zone (e.g. Houston Load Zone) to see its point children."
            )
            return "\n".join([head] + _render_groups(grouped))
        return None

    if var_filter:
        return _answer_locations_publishing(
            key,
            label,
            var_filter,
            message=message,
            criteria=criteria,
            granularity=granularity,
            agg_wanted=agg_wanted,
            point_wanted=point_wanted,
            out_list=out_list,
        )

    sections: List[str] = []
    if granularity in ("aggregate", "both") and grouped:
        total = sum(len(v) for v in grouped.values())
        scope_txt = ""
        if agg_wanted:
            scope_txt = " " + ", ".join(sorted({_label_for_type(w) for w in agg_wanted}))
        head = (
            f"{label} has {total}{scope_txt} aggregate location"
            f"{'s' if total != 1 else ''} you can query:"
        )
        sections.append("\n".join([head] + _render_groups(grouped)))
        if granularity == "aggregate" and not agg_wanted:
            sections.append(_aggregation_blurb(key, location_types))
        for names in grouped.values():
            listed_names.extend(names)

    if granularity in ("point", "both"):
        points = _safe_point_resources(key, allowed_entities)
        point_grouped = _grouped_resources(points, point_wanted)
        if point_grouped:
            total = sum(len(v) for v in point_grouped.values())
            if domain == "weather":
                head = (
                    f"{label} has {total} independent **weather** point location"
                    f"{'s' if total != 1 else ''} "
                    "(unique `weather_sims_id`, `is_aggregate = false`). "
                    "City rows are population-weighted weather; solar/wind rows "
                    "are farm-sited weather — not energy MW."
                )
            else:
                head = (
                    f"{label} has {total} **point** site"
                    f"{'s' if total != 1 else ''} "
                    "(`is_aggregate = false` — cities, plant areas, stations):"
                )
            sections.append("\n".join([head] + _render_groups(point_grouped, point=True)))
            for names in point_grouped.values():
                listed_names.extend(names)
        elif granularity == "point":
            n_point = (
                (location_types.get(key) or {})
                .get("aggregation", {})
                .get("point_locations")
            )
            extra = f" ({n_point} in the catalog counts)" if n_point else ""
            sections.append(
                f"{label} has point weather/energy sites{extra}, but I "
                "couldn't load their names just now. Name a city or ask what "
                "makes up a specific zone."
            )

    if not sections:
        scope = ", ".join(sorted(_label_for_type(w) for w in agg_wanted or point_wanted)) or "that type"
        return f"{label} doesn't have any {scope} in the catalog."
    _record_listed(
        out_list,
        key=key,
        label=label,
        granularity=granularity,
        domain=domain or "",
        names=listed_names,
    )
    return "\n\n".join(sections)


def _answer_locations_publishing(
    key: str,
    label: str,
    variables: Sequence[str],
    *,
    message: str,
    criteria: Dict[str, Any],
    granularity: str,
    agg_wanted: set,
    point_wanted: set,
    out_list: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    rows = _safe_locations_for_variables(key, variables)
    shown = ", ".join(f"`{v}`" for v in variables)
    if not rows:
        return f"{label} has no locations that publish {shown}."

    explicit_granularity = str((criteria or {}).get("granularity") or "").strip().lower()
    if not explicit_granularity and granularity == "aggregate":
        text = (message or "").lower()
        zone_ask = any(
            z in text.split() for z in ("zone", "zones", "region", "regions")
        )
        if not zone_ask:
            granularity = "both"

    seen = set()
    resources: List[Dict[str, Any]] = []
    for row in rows:
        name = str(row.get("resource_name") or row.get("location_name") or "").strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        resources.append(
            {
                "resource_name": name,
                "resource_type": row.get("resource_type") or "",
                "is_aggregate": bool(row.get("is_aggregate")),
            }
        )

    aggregates = [r for r in resources if r.get("is_aggregate")]
    points = [r for r in resources if not r.get("is_aggregate")]
    listed_names: List[str] = []
    sections: List[str] = []

    if granularity in ("aggregate", "both"):
        grouped = _grouped_resources(aggregates, agg_wanted)
        if grouped:
            total = sum(len(v) for v in grouped.values())
            head = (
                f"{label} has {total} aggregate location"
                f"{'s' if total != 1 else ''} that publish {shown}:"
            )
            sections.append("\n".join([head] + _render_groups(grouped)))
            for names in grouped.values():
                listed_names.extend(names)

    if granularity in ("point", "both"):
        grouped = _grouped_resources(points, point_wanted)
        if grouped:
            total = sum(len(v) for v in grouped.values())
            head = (
                f"{label} has {total} point site"
                f"{'s' if total != 1 else ''} that publish {shown}:"
            )
            sections.append("\n".join([head] + _render_groups(grouped, point=True)))
            for names in grouped.values():
                listed_names.extend(names)

    if not sections:
        return f"{label} has no locations that publish {shown} at that granularity."
    _record_listed(
        out_list,
        key=key,
        label=label,
        granularity=granularity,
        domain="",
        names=listed_names,
    )
    return "\n\n".join(sections)


def _record_listed(
    out_list: Optional[List[Dict[str, Any]]],
    *,
    key: str,
    label: str,
    granularity: str,
    domain: str,
    names: Sequence[str],
) -> None:
    if out_list is None:
        return
    clean = [n for n in names if n]
    if not clean:
        return
    out_list.append(
        {
            "key": "catalog_location_list",
            "kind": "catalog_location_list",
            "entity": key,
            "entity_label": label,
            "granularity": granularity,
            "domain": domain,
            "names": clean,
        }
    )


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
    *,
    message: str = "",
    catalog_locations: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Variables available for named / last-listed places, else per location type."""
    if not key:
        return None

    named = [str(v).strip() for v in (aep.query.location.values or []) if str(v).strip()]
    prior_names: List[str] = []
    prior_domain = ""
    if catalog_locations and str(catalog_locations.get("entity") or "") in {key, ""}:
        prior_names = [str(n) for n in (catalog_locations.get("names") or []) if n]
        prior_domain = str(catalog_locations.get("domain") or "")
    names = named or prior_names
    domain = infer_domain(message, aep.query.location.criteria) or prior_domain

    if names:
        scoped = _answer_variables_for_named_places(
            key, label, names, variable_catalog, domain=domain
        )
        if scoped:
            return scoped

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


def _answer_variables_for_named_places(
    key: str,
    label: str,
    names: Sequence[str],
    variable_catalog: Sequence[Dict[str, Any]],
    *,
    domain: str = "",
) -> Optional[str]:
    try:
        rows = metadata_db.load_variables_for_locations(key, list(names))
    except Exception:
        return None
    if not rows:
        return None
    domain_l = (domain or "").lower()
    if domain_l in ("weather", "energy"):
        rows = [
            r
            for r in rows
            if str(r.get("variable_type") or "").lower() == domain_l
        ]
    if not rows:
        return None

    by_name = {str(e.get("variable")): e for e in variable_catalog or []}
    by_place: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        place = str(row.get("place_name") or row.get("location_name") or row.get("resource_name") or "")
        var = row.get("variable") or ""
        if not place or not var:
            continue
        bucket = by_place.setdefault(
            place,
            {
                "is_aggregate": bool(row.get("is_aggregate")),
                "resource_type": row.get("resource_type") or "",
                "vars": [],
                "types": {},
            },
        )
        if var not in bucket["vars"]:
            bucket["vars"].append(var)
        vtype = str(row.get("variable_type") or "other").replace("_", " ").title()
        bucket["types"].setdefault(vtype, [])
        if var not in bucket["types"][vtype]:
            bucket["types"][vtype].append(var)

    # Preserve ask order; append any extras the join returned.
    shown: List[str] = []
    for n in names:
        hit = next((p for p in by_place if p.lower() == n.lower()), None)
        if hit and hit not in shown:
            shown.append(hit)
    for p in by_place:
        if p not in shown:
            shown.append(p)
    missing = [n for n in names if n.lower() not in {p.lower() for p in shown}]

    n = len(shown)
    if domain_l == "weather":
        src = "`location_variables`"
    elif domain_l == "energy":
        src = "`resource_variables`"
    else:
        src = "`location_variables` (weather) and `resource_variables` (energy)"
    head = (
        f"{label} — variables forecasted at the {n} named place"
        f"{'s' if n != 1 else ''} "
        f"(from {src}; not the full {label} variable catalog):"
    )
    lines = [head]
    for place in shown:
        info = by_place[place]
        kind = "aggregate zone" if info["is_aggregate"] else "point site"
        lines.append("")
        lines.append(f"**{place}** ({kind})")
        for category in sorted(info["types"]):
            lines.append(f"*{category}*")
            for var in info["types"][category]:
                lines.append(_format_variable_line(var, by_name))
    if missing:
        lines.append("")
        lines.append("No variables linked for: " + ", ".join(missing))
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
    "from these",
    "from those",
    "these locations",
    "those locations",
    "these sites",
    "those sites",
    "these point",
    "those point",
    "their variables",
    "forecasted from",
    "forecast from",
    "at these",
    "for these",
)


def _is_variables_by_location(
    message: str, flagged: List[str], catalog_locations: Optional[Dict[str, Any]] = None
) -> bool:
    """True when the ask is variables at specific / last-listed places."""
    tokens = set(tokenize(message))
    has_var = bool(tokens & _TARGET_KEYWORDS["variables"])
    has_loc = bool(tokens & _TARGET_KEYWORDS["locations"])
    lower = (message or "").lower()
    deictic = any(p in lower for p in _CROSS_LOCATION_PHRASES) or "these" in tokens or "those" in tokens
    prior = bool((catalog_locations or {}).get("names"))
    if has_var and deictic:
        return True
    if has_var and prior and (has_loc or deictic or "from" in tokens):
        return True
    if not (has_var and has_loc):
        # LLM1 may flag both dimensions even when the wording is one-sided
        if "variables" in flagged and "locations" in flagged and has_var:
            return deictic or "per" in tokens or prior
        return False
    if deictic or "per" in tokens:
        return True
    # Both catalogs named with no clear "list of locations" framing → cross
    return "variables" in flagged and "locations" in flagged


def _pick_target(
    aep: AnalyticalExecutionPlan,
    message: str,
    catalog_locations: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    flagged = _flagged_targets(aep)
    named_places = [
        str(v).strip() for v in (aep.query.location.values or []) if str(v).strip()
    ]
    tokens = set(tokenize(message))
    has_var = bool(tokens & _TARGET_KEYWORDS["variables"])
    if has_var and named_places:
        return "variables_by_location"
    if _is_variables_by_location(message, flagged, catalog_locations):
        return "variables_by_location"
    keyword = _keyword_target(message)
    # The wording wins whenever the plan left that door open. LLM1 routinely
    # flags `location` as boilerplate on every metadata plan, so trusting the
    # dimension order alone answers the wrong question.
    if keyword and (not flagged or keyword in flagged):
        # "variables for these locations" with only variables flagged still
        # must not dump the entity-wide list when a prior location list exists.
        if (
            keyword == "variables"
            and catalog_locations
            and catalog_locations.get("names")
            and _is_variables_by_location(message, flagged + ["variables", "locations"], catalog_locations)
        ):
            return "variables_by_location"
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
    location_types: Optional[Dict[str, Any]] = None,
    catalog_locations: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Answer a resolved metadata plan, or None when it needs the normal path.

    Returns (answer_text, location_list_ref). The ref is set when this turn
    listed places so a follow-up can scope variables to them.
    """
    target = _pick_target(aep, message, catalog_locations)
    if target == "entities" and not _entity_key(rep):
        return answer_entities(allowed_entities), None

    if target not in (
        "variables",
        "variables_by_location",
        "initializations",
        "locations",
    ):
        if not (rep and rep.locations and rep.locations.mode == "metadata_query"):
            return None, None
        target = "locations"

    sections: List[str] = []
    empty: List[str] = []
    listed: List[Dict[str, Any]] = []
    for key, label in _entity_targets(aep, rep, allowed_entities):
        if target == "variables_by_location":
            section = answer_variables_by_location(
                aep,
                key,
                label,
                entity_catalog,
                entity_variables,
                variable_catalog,
                message=message,
                catalog_locations=catalog_locations,
            )
        elif target == "variables":
            section = answer_variables(key, label, entity_variables, variable_catalog)
        elif target == "initializations":
            section = answer_initializations(key, label, latest_inits)
        else:
            section = answer_locations(
                aep,
                key,
                label,
                entity_catalog,
                message=message,
                allowed_entities=allowed_entities,
                location_types=location_types or {},
                out_list=listed,
                catalog_locations=catalog_locations,
                variable_catalog=variable_catalog,
            )
        if section:
            sections.append(section)
        else:
            empty.append(label)

    # Nothing at all to show: fall back to the normal path rather than assert
    # emptiness we cannot stand behind.
    if not sections:
        return None, None
    # Say so explicitly when one of several named entities had nothing, so a
    # multi-entity ask never looks like it quietly dropped one.
    if empty:
        sections.append(f"Nothing available in the catalog for {', '.join(empty)}.")
    loc_ref = listed[-1] if listed else None
    return "\n\n".join(sections), loc_ref
