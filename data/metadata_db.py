"""Metadata DB — ACL, entities, latest inits, catalog."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.pool

from config.settings import settings
from data.pg_pool import acquire_connection, is_stale_connection_error, pool_connect_kwargs, release_connection
from security.acl import UserACL

logger = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
_latest_inits_cache: Dict[Tuple[str, str, str], datetime] = {}
_latest_inits_ts: float = 0.0
_latest_inits_lock = threading.Lock()
_LATEST_INITS_TTL_SEC = 600
WEATHER_FORECAST_LONG_CADENCE_HOURS = 6
_variable_units_cache: Dict[str, str] = {}
_variable_meta_cache: Dict[str, Dict[str, str]] = {}
_entity_catalog_cache: Dict[str, Dict[str, Any]] = {}
_entity_catalog_ts: float = 0.0
_entity_catalog_lock = threading.Lock()
_ENTITY_CATALOG_TTL_SEC = 86400  # 1 day


def init_pool(min_conn: int = 1, max_conn: int = 5) -> None:
    global _pool
    if not settings.metadata_db.host:
        logger.warning("METADATA_DB_HOST not set; metadata pool skipped")
        return
    _pool = psycopg2.pool.ThreadedConnectionPool(
        min_conn, max_conn, **pool_connect_kwargs(settings.metadata_db)
    )
    logger.info("Metadata DB pool initialized")


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def get_connection():
    if _pool is None:
        raise RuntimeError("Metadata DB pool not initialized")
    conn, discard = acquire_connection(_pool, log_name="Metadata DB")
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        if is_stale_connection_error(e):
            discard = True
        raise
    finally:
        release_connection(_pool, conn, close=discard)


def ping() -> bool:
    if _pool is None:
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1


def load_user_acl(username: str) -> UserACL:
    acl = UserACL(username=username)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ue.entity_id, e.shortname, e.timezone
                FROM user_entities ue
                JOIN entities e ON e.entity_id = ue.entity_id
                WHERE ue.username = %s
                """,
                (username,),
            )
            for entity_id, shortname, tz in cur.fetchall():
                acl.entity_ids.append(str(entity_id))
                if shortname:
                    acl.project_names.append(shortname)
                    if tz:
                        acl.timezone_by_project[shortname] = tz
    return acl


def load_allowed_entities(entity_ids: List[str]) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id, entity, shortname, timezone
                FROM entities
                WHERE entity_id::text = ANY(%s)
                  AND is_iso = true
                  AND has_forecast = true
                ORDER BY shortname
                """,
                (entity_ids,),
            )
            return [
                {
                    "entity_id": str(r[0]),
                    "entity": r[1],
                    "shortname": r[2],
                    "timezone": r[3],
                }
                for r in cur.fetchall()
            ]


def load_variable_units_cache() -> Dict[str, str]:
    """Load variable units/type/name once at startup (restart app when variables change).

    `variables` carries a `variable_type` ('weather' | 'energy') for every row, so the
    catalog never has to guess a category. A code can appear on several rows (different
    units or display labels); the type is consistent across them.
    """
    global _variable_units_cache, _variable_meta_cache
    if _pool is None:
        logger.warning("Metadata DB pool not initialized; variable_units cache skipped")
        _variable_units_cache = {}
        _variable_meta_cache = {}
        return _variable_units_cache

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT variable, units, variable_type::text, variable_name
                FROM variables
                WHERE variable IS NOT NULL
                ORDER BY variable
                """
            )
            rows = cur.fetchall()

    units: Dict[str, str] = {}
    meta: Dict[str, Dict[str, str]] = {}
    for variable, unit, variable_type, variable_name in rows:
        if not variable:
            continue
        name = str(variable)
        units[name] = str(unit or "")
        entry = meta.setdefault(name, {"units": "", "variable_type": "", "variable_name": ""})
        entry["units"] = str(unit or "")
        if variable_type:
            entry["variable_type"] = str(variable_type)
        if variable_name:
            entry["variable_name"] = str(variable_name)

    _variable_units_cache = units
    _variable_meta_cache = meta
    logger.info("Loaded %d variable units into cache", len(_variable_units_cache))
    return _variable_units_cache


def get_variable_units() -> Dict[str, str]:
    return dict(_variable_units_cache)


def get_variable_meta() -> Dict[str, Dict[str, str]]:
    """variable → {units, variable_type, variable_name} straight from the catalog."""
    return {k: dict(v) for k, v in _variable_meta_cache.items()}


def floor_weather_long_init(init: datetime) -> datetime:
    """Floor a weather forecast init to the UTC 6h grid for short+extended UNION ALL.

    Extended weather forecast rows publish on 00/06/12/18 UTC; short is hourly.
    Use the floored anchor when the query spans beyond init+18h.
    """
    if init.tzinfo is None:
        dt = init.replace(tzinfo=timezone.utc)
    else:
        dt = init.astimezone(timezone.utc)
    dt = dt.replace(minute=0, second=0, microsecond=0)
    cadence = WEATHER_FORECAST_LONG_CADENCE_HOURS
    return dt.replace(hour=(dt.hour // cadence) * cadence)


def get_latest_inits_by_project(force: bool = False) -> Dict[Tuple[str, str, str], datetime]:
    global _latest_inits_cache, _latest_inits_ts
    now = time.monotonic()
    with _latest_inits_lock:
        if not force and _latest_inits_cache and (now - _latest_inits_ts) < _LATEST_INITS_TTL_SEC:
            return dict(_latest_inits_cache)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
            cur.execute(
                """
                SELECT e.shortname, er.ensemble_type::text, er.ensemble_window::text, MAX(er.initialization)
                FROM ensemble_runs er
                JOIN entities e ON e.entity_id = er.entity_id
                WHERE er.active = true AND er.complete = true
                GROUP BY e.shortname, er.ensemble_type, er.ensemble_window
                """
            )
            result = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}
    with _latest_inits_lock:
        _latest_inits_cache = result
        _latest_inits_ts = now
    return dict(result)


def get_latest_inits_nested(shortnames: List[str], force: bool = False) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Shape per prompt §3: latest_inits[shortname][type][window] -> ISO string."""
    flat = get_latest_inits_by_project(force=force)
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for shortname in shortnames:
        bucket: Dict[str, Dict[str, str]] = {
            "weather": {},
            "energy": {},
            "fundamental_market": {},
        }
        for (proj, etype, window), ts in flat.items():
            if proj != shortname:
                continue
            if etype in bucket and ts is not None:
                bucket[etype][window] = ts.isoformat()
        weather_forecast = flat.get((shortname, "weather", "forecast"))
        if weather_forecast is not None:
            bucket["weather"]["forecast_long"] = floor_weather_long_init(
                weather_forecast
            ).isoformat()
        out[shortname] = bucket
    return out


def load_entity_locations(entity_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.location_name, l.weather_sims_id, r.energy_sims_id, l.is_aggregate
                FROM resources r
                JOIN locations l ON l.location_id = r.location_id
                WHERE r.entity_id = %s
                ORDER BY l.is_aggregate DESC, l.location_name
                """,
                (entity_id,),
            )
            return [
                {
                    "location_name": r[0],
                    "weather_sims_id": r[1],
                    "energy_sims_id": r[2],
                    "is_aggregate": bool(r[3]),
                }
                for r in cur.fetchall()
            ]


def load_location_granularity_stats(
    entity_ids: List[str],
) -> Dict[str, Dict[str, int]]:
    """Per-entity counts of aggregate vs point locations and weight-table usage.

    Does not return names — LLM1 gets counts only. Keyed by entity shortname.
    """
    if not entity_ids:
        return {}
    id_set = {str(x) for x in entity_ids}
    stats: Dict[str, Dict[str, int]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
            cur.execute(
                """
                SELECT e.shortname,
                       COUNT(DISTINCT l.location_id)
                         FILTER (WHERE COALESCE(l.is_aggregate, false)) AS aggregate_locations,
                       COUNT(DISTINCT l.location_id)
                         FILTER (WHERE NOT COALESCE(l.is_aggregate, false)) AS point_locations
                FROM resources r
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN locations l ON l.location_id = r.location_id
                WHERE r.entity_id::text = ANY(%s)
                GROUP BY e.shortname
                """,
                (list(id_set),),
            )
            for shortname, n_agg, n_point in cur.fetchall():
                if not shortname:
                    continue
                stats[shortname] = {
                    "aggregate_locations": int(n_agg or 0),
                    "point_locations": int(n_point or 0),
                    "weighted_parents": 0,
                    "weighted_children": 0,
                }
            cur.execute(
                """
                SELECT e.shortname,
                       COUNT(DISTINCT lw.parent_location_id) AS weighted_parents,
                       COUNT(DISTINCT lw.location_id) AS weighted_children
                FROM location_weights lw
                JOIN locations parent ON parent.location_id = lw.parent_location_id
                JOIN resources r ON r.location_id = parent.location_id
                JOIN entities e ON e.entity_id = r.entity_id
                WHERE e.entity_id::text = ANY(%s)
                GROUP BY e.shortname
                """,
                (list(id_set),),
            )
            for shortname, n_parents, n_children in cur.fetchall():
                if not shortname:
                    continue
                bucket = stats.setdefault(
                    shortname,
                    {
                        "aggregate_locations": 0,
                        "point_locations": 0,
                        "weighted_parents": 0,
                        "weighted_children": 0,
                    },
                )
                bucket["weighted_parents"] = int(n_parents or 0)
                bucket["weighted_children"] = int(n_children or 0)
    return stats


def load_entity_point_resources(
    entity_ids: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Point-site resources (`is_aggregate = false`) keyed by entity shortname."""
    if not entity_ids:
        return {}
    id_set = {str(x) for x in entity_ids}
    out: Dict[str, List[Dict[str, Any]]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
            cur.execute(
                """
                SELECT e.shortname,
                       r.resource_name,
                       r.energy_sims_id,
                       l.weather_sims_id,
                       rt.resource_type
                FROM resources r
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                JOIN locations l ON l.location_id = r.location_id
                WHERE r.entity_id::text = ANY(%s)
                  AND COALESCE(l.is_aggregate, false) = false
                ORDER BY e.shortname, rt.resource_type, r.resource_name
                """,
                (list(id_set),),
            )
            for shortname, name, energy_id, weather_id, resource_type in cur.fetchall():
                if not shortname:
                    continue
                out.setdefault(shortname, []).append(
                    {
                        "resource_name": name or "",
                        "energy_sims_id": energy_id or "",
                        "weather_sims_id": weather_id or "",
                        "resource_type": resource_type or "",
                        "is_aggregate": False,
                    }
                )
    return out


def load_location_composition(
    shortname: str,
    parent_needles: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Child point sites that weight into an entity's aggregate parent(s)."""
    if not shortname:
        return []
    needles = [str(n).strip() for n in (parent_needles or []) if str(n).strip()]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
            sql = """
                SELECT DISTINCT
                       p.location_name,
                       p.weather_sims_id,
                       c.location_name,
                       c.weather_sims_id,
                       vi.variable,
                       vo.variable,
                       lw.weight,
                       lw.is_dynamic
                FROM location_weights lw
                JOIN locations p ON p.location_id = lw.parent_location_id
                JOIN locations c ON c.location_id = lw.location_id
                JOIN resources r ON r.location_id = p.location_id
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN variables vi ON vi.variable_id = lw.input_variable_id
                JOIN variables vo ON vo.variable_id = lw.output_variable_id
                WHERE e.shortname = %s
            """
            params: List[Any] = [shortname]
            if needles:
                likes = " OR ".join(
                    ["(LOWER(p.location_name) LIKE %s OR LOWER(p.weather_sims_id) LIKE %s)"]
                    * len(needles)
                )
                sql += " AND (" + likes + ")"
                for n in needles:
                    pat = f"%{n.lower()}%"
                    params.extend([pat, pat])
            sql += " ORDER BY p.location_name, vo.variable, c.location_name"
            cur.execute(sql, params)
            rows = cur.fetchall()
    return [
        {
            "parent_name": r[0] or "",
            "parent_weather_sims_id": r[1] or "",
            "child_name": r[2] or "",
            "child_weather_sims_id": r[3] or "",
            "input_variable": r[4] or "",
            "output_variable": r[5] or "",
            "weight": float(r[6]) if r[6] is not None else None,
            "is_dynamic": bool(r[7]),
        }
        for r in rows
    ]


def load_variables_for_locations(
    shortname: str,
    location_names: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Weather + energy variables linked to named places.

    Weather comes from ``location_variables`` (any ``locations`` row, aggregate
    or point). Energy comes from ``resource_variables`` (usually zones /
    portfolio; point sites are often empty). Names match location_name,
    resource_name, or either sims id.
    """
    if not shortname:
        return []
    names = [str(n).strip() for n in (location_names or []) if str(n).strip()]
    lower_names = [n.lower() for n in names]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '15000'")
            name_filter = ""
            params: List[Any] = [shortname]
            if lower_names:
                name_filter = """
                  AND (
                    LOWER(l.location_name) = ANY(%s)
                    OR LOWER(r.resource_name) = ANY(%s)
                    OR LOWER(COALESCE(l.weather_sims_id, '')) = ANY(%s)
                    OR LOWER(COALESCE(r.energy_sims_id, '')) = ANY(%s)
                  )
                """
                params.extend([lower_names, lower_names, lower_names, lower_names])
            sql = f"""
                SELECT DISTINCT
                       l.location_name,
                       r.resource_name,
                       l.weather_sims_id,
                       r.energy_sims_id,
                       COALESCE(l.is_aggregate, false),
                       rt.resource_type,
                       v.variable,
                       v.variable_name,
                       v.variable_type,
                       v.units,
                       'location_variables'::text AS link
                FROM resources r
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                JOIN locations l ON l.location_id = r.location_id
                JOIN location_variables lv ON lv.location_id = l.location_id
                JOIN variables v ON v.variable_id = lv.variable_id
                WHERE e.shortname = %s
                {name_filter}
                UNION
                SELECT DISTINCT
                       l.location_name,
                       r.resource_name,
                       l.weather_sims_id,
                       r.energy_sims_id,
                       COALESCE(l.is_aggregate, false),
                       rt.resource_type,
                       v.variable,
                       v.variable_name,
                       v.variable_type,
                       v.units,
                       'resource_variables'::text AS link
                FROM resources r
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                JOIN locations l ON l.location_id = r.location_id
                JOIN resource_variables rv ON rv.resource_id = r.resource_id
                JOIN variables v ON v.variable_id = rv.variable_id
                WHERE e.shortname = %s
                {name_filter}
                ORDER BY 2, 7
            """
            weather_params = list(params)
            energy_params = [shortname]
            if lower_names:
                energy_params.extend([lower_names, lower_names, lower_names, lower_names])
            cur.execute(sql, weather_params + energy_params)
            rows = cur.fetchall()
    asked = set(lower_names)
    out: List[Dict[str, Any]] = []
    for r in rows:
        loc_name, res_name = r[0] or "", r[1] or ""
        place = res_name or loc_name
        if asked:
            for cand in (res_name, loc_name, r[2] or "", r[3] or ""):
                if cand.lower() in asked:
                    place = cand
                    break
        out.append(
            {
                "place_name": place,
                "location_name": loc_name,
                "resource_name": res_name,
                "weather_sims_id": r[2] or "",
                "energy_sims_id": r[3] or "",
                "is_aggregate": bool(r[4]),
                "resource_type": r[5] or "",
                "variable": r[6] or "",
                "variable_name": r[7] or "",
                "variable_type": r[8] or "",
                "units": r[9] or "",
                "link": r[10] or "",
            }
        )
    return out


def load_entity_catalog(
    entity_ids: List[str],
    force: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Resource/location phone book keyed by entity shortname. Cached 1 day.

    Each value:
      {
        "portfolio": {"energy_sims_id": "...", "weather_sims_id": "..."} | null,
        "resources": [
          {
            "resource_name": "...",
            "energy_sims_id": "...",
            "weather_sims_id": "...",
            "resource_type": "...",
            "is_aggregate": bool
          },
          ...
        ]
      }
    """
    global _entity_catalog_cache, _entity_catalog_ts
    if not entity_ids:
        return {}

    id_set = {str(x) for x in entity_ids}
    now = time.monotonic()

    with _entity_catalog_lock:
        cache_fresh = (
            not force
            and _entity_catalog_cache
            and (now - _entity_catalog_ts) < _ENTITY_CATALOG_TTL_SEC
        )
        if cache_fresh:
            cached_ids = {
                str(v.get("entity_id"))
                for v in _entity_catalog_cache.values()
                if v.get("entity_id")
            }
            if id_set <= cached_ids:
                return _catalog_public_view(_entity_catalog_cache, id_set)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '30000'")
            cur.execute(
                """
                SELECT e.entity_id::text,
                       e.shortname,
                       r.resource_name,
                       r.energy_sims_id,
                       l.weather_sims_id,
                       rt.resource_type,
                       COALESCE(l.is_aggregate, false)
                FROM resources r
                JOIN entities e ON e.entity_id = r.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                LEFT JOIN locations l ON l.location_id = r.location_id
                WHERE r.entity_id::text = ANY(%s)
                  AND (
                    COALESCE(l.is_aggregate, false) = true
                    OR rt.resource_type = 'portfolio'
                  )
                ORDER BY e.shortname, rt.resource_type, r.resource_name
                """,
                (list(id_set),),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                SELECT entity_id::text, shortname
                FROM entities
                WHERE entity_id::text = ANY(%s)
                """,
                (list(id_set),),
            )
            entity_rows = cur.fetchall()

    built: Dict[str, Dict[str, Any]] = {}
    for eid, shortname in entity_rows:
        if shortname:
            built[shortname] = {
                "entity_id": str(eid),
                "portfolio": None,
                "resources": [],
            }

    for (
        entity_id,
        shortname,
        resource_name,
        energy_sims_id,
        weather_sims_id,
        resource_type,
        is_aggregate,
    ) in rows:
        if not shortname:
            continue
        bucket = built.setdefault(
            shortname,
            {
                "entity_id": str(entity_id),
                "portfolio": None,
                "resources": [],
            },
        )
        entry = {
            "resource_name": resource_name or "",
            "energy_sims_id": energy_sims_id or "",
            "weather_sims_id": weather_sims_id or "",
            "resource_type": resource_type or "",
            "is_aggregate": bool(is_aggregate),
        }
        if (resource_type or "").lower() == "portfolio" and bucket["portfolio"] is None:
            bucket["portfolio"] = {
                "energy_sims_id": entry["energy_sims_id"],
                "weather_sims_id": entry["weather_sims_id"],
            }
        bucket["resources"].append(entry)

    with _entity_catalog_lock:
        merged = dict(_entity_catalog_cache)
        merged.update(built)
        _entity_catalog_cache = merged
        _entity_catalog_ts = now
        return _catalog_public_view(_entity_catalog_cache, id_set)


def _catalog_public_view(
    catalog: Dict[str, Dict[str, Any]],
    entity_ids: set,
) -> Dict[str, Dict[str, Any]]:
    """Strip internal entity_id; keep only requested entities."""
    out: Dict[str, Dict[str, Any]] = {}
    for shortname, bucket in catalog.items():
        if str(bucket.get("entity_id")) not in entity_ids:
            continue
        out[shortname] = {
            "portfolio": bucket.get("portfolio"),
            "resources": list(bucket.get("resources") or []),
        }
    return out


def load_entity_variables(entity_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Variables linked to each entity via resource_variables ∪ location_variables.

    Returns shortname → {
      "variables": sorted list of canonical names (energy ∪ weather),
      "weather": sorted weather names from location_variables,
      "energy_by_resource_type": { variable: sorted resource_types },
      "variables_by_resource_type": { resource_type: sorted variables },
    }
    """
    if not entity_ids:
        return {}
    id_set = {str(x) for x in entity_ids}
    out: Dict[str, Dict[str, Any]] = {}

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '30000'")
            cur.execute(
                """
                SELECT e.shortname, v.variable, rt.resource_type
                FROM entities e
                JOIN resources r ON r.entity_id = e.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                JOIN resource_variables rv ON rv.resource_id = r.resource_id
                JOIN variables v ON v.variable_id = rv.variable_id
                WHERE e.entity_id::text = ANY(%s)
                  AND e.shortname IS NOT NULL
                  AND v.variable IS NOT NULL
                """,
                (list(id_set),),
            )
            energy_rows = cur.fetchall()

            # Weather is linked to locations; join through resources so we know
            # which resource type (load zone, wx zone, …) carries each weather var.
            cur.execute(
                """
                SELECT DISTINCT e.shortname, v.variable, rt.resource_type
                FROM entities e
                JOIN resources r ON r.entity_id = e.entity_id
                JOIN resource_types rt ON rt.resource_type_id = r.resource_type_id
                JOIN locations l ON l.location_id = r.location_id
                JOIN location_variables lv ON lv.location_id = l.location_id
                JOIN variables v ON v.variable_id = lv.variable_id
                WHERE e.entity_id::text = ANY(%s)
                  AND e.shortname IS NOT NULL
                  AND v.variable IS NOT NULL
                """,
                (list(id_set),),
            )
            weather_rows = cur.fetchall()

            cur.execute(
                """
                SELECT shortname FROM entities
                WHERE entity_id::text = ANY(%s) AND shortname IS NOT NULL
                """,
                (list(id_set),),
            )
            for (shortname,) in cur.fetchall():
                out[shortname] = {
                    "variables": set(),
                    "weather": set(),
                    "energy_by_resource_type": {},
                    "variables_by_resource_type": {},
                }

    def _bucket(shortname: str) -> Dict[str, Any]:
        return out.setdefault(
            shortname,
            {
                "variables": set(),
                "weather": set(),
                "energy_by_resource_type": {},
                "variables_by_resource_type": {},
            },
        )

    def _add_to_type(bucket: Dict[str, Any], variable: str, resource_type: Any) -> None:
        rt = str(resource_type or "").lower()
        if not rt:
            return
        bucket["variables_by_resource_type"].setdefault(rt, set()).add(variable)

    for shortname, variable, resource_type in energy_rows:
        bucket = _bucket(shortname)
        bucket["variables"].add(variable)
        by_rt = bucket["energy_by_resource_type"].setdefault(variable, set())
        if resource_type:
            by_rt.add(str(resource_type).lower())
        _add_to_type(bucket, variable, resource_type)

    for shortname, variable, resource_type in weather_rows:
        bucket = _bucket(shortname)
        bucket["variables"].add(variable)
        bucket["weather"].add(variable)
        _add_to_type(bucket, variable, resource_type)

    # Serialize sets for JSON-friendly resolver payload
    serialized: Dict[str, Dict[str, Any]] = {}
    for shortname, bucket in out.items():
        serialized[shortname] = {
            "variables": sorted(bucket["variables"]),
            "weather": sorted(bucket["weather"]),
            "energy_by_resource_type": {
                var: sorted(rts)
                for var, rts in sorted(bucket["energy_by_resource_type"].items())
            },
            "variables_by_resource_type": {
                rt: sorted(vars_)
                for rt, vars_ in sorted(bucket["variables_by_resource_type"].items())
            },
        }
    return serialized


def resolve_location(entity_id: str, name_or_key: str) -> Optional[Dict[str, str]]:
    needle = (name_or_key or "").strip().lower()
    if not needle:
        return None
    for loc in load_entity_locations(entity_id):
        candidates = [
            loc.get("location_name", ""),
            loc.get("weather_sims_id", ""),
            loc.get("energy_sims_id", ""),
        ]
        for c in candidates:
            if c and needle in c.lower():
                return {
                    "location_name": loc["location_name"],
                    "weather_sims_id": loc.get("weather_sims_id") or "",
                    "energy_sims_id": loc.get("energy_sims_id") or "",
                    "is_aggregate": bool(loc.get("is_aggregate")),
                }
    return None


def execute_query(sql: str, params: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
    """Execute read-only catalog or historical SQL on Metadata Postgres."""
    import time
    from config.settings import settings
    from data.query_result import build_result, serialize_row
    from security.sql_guard import ensure_outer_limit, validate_sql

    sql = ensure_outer_limit(sql)
    validate_sql(sql)
    cap = settings.safety.max_query_rows
    timeout_ms = settings.safety.query_timeout_sec * 1000

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = ON")
            cur.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")
            t0 = time.monotonic()
            cur.execute(sql, params or None)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            raw_rows = cur.fetchmany(cap + 1)
            elapsed = time.monotonic() - t0

    truncated = len(raw_rows) > cap
    if truncated:
        raw_rows = raw_rows[:cap]
    rows = [serialize_row(r) for r in raw_rows]
    return build_result(columns, rows, backend="metadata", query_time_ms=elapsed * 1000, truncated=truncated)
