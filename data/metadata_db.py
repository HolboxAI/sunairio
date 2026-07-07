"""Metadata DB — ACL, entities, latest inits, catalog."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime
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
_variable_units_cache: Dict[str, str] = {}


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
                SELECT entity_id, entity, shortname, timezone, is_iso, has_forecast
                FROM entities
                WHERE entity_id::text = ANY(%s)
                  AND (is_iso = true OR has_forecast = true)
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
                    "is_iso": bool(r[4]),
                    "has_forecast": bool(r[5]),
                }
                for r in cur.fetchall()
            ]


def load_variable_units_cache() -> Dict[str, str]:
    """Load variable → units map once at startup (restart app when variables table changes)."""
    global _variable_units_cache
    if _pool is None:
        logger.warning("Metadata DB pool not initialized; variable_units cache skipped")
        _variable_units_cache = {}
        return _variable_units_cache
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT variable, units
                FROM variables
                WHERE variable IS NOT NULL
                ORDER BY variable
                """
            )
            _variable_units_cache = {
                str(r[0]): str(r[1] or "") for r in cur.fetchall() if r[0]
            }
    logger.info("Loaded %d variable units into cache", len(_variable_units_cache))
    return _variable_units_cache


def get_variable_units() -> Dict[str, str]:
    return dict(_variable_units_cache)


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
                }
    return None
