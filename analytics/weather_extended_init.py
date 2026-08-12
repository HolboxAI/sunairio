"""Resolve weather_forecast_ensemble_extended initialization with UTC walk-back.

Extended weather publishes on UTC 00/06/12/18. The hourly short init can advance
before the matching extended batch lands — floor to the 6h grid, then walk back
until Forecast DB shows rows (ensemble_path=1 probe).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from data.metadata_db import WEATHER_FORECAST_LONG_CADENCE_HOURS, floor_weather_long_init

logger = logging.getLogger(__name__)

PROBE_PATH = 1
DEFAULT_PROBE_VARIABLE = "temp_2m"
DEFAULT_LOOKBACK_HOURS = 24


def probe_location_from_context(
    entity_name: str,
    entity_catalog: dict,
    locations: Optional[object],
) -> Optional[str]:
    """Pick a catalog location key for extended-table probes."""
    if locations is not None:
        values = getattr(locations, "values", None) or []
        for loc in values:
            if not isinstance(loc, dict):
                continue
            wid = str(loc.get("weather_sims_id") or "").strip()
            if wid:
                return wid
    bucket = entity_catalog.get(entity_name) or {}
    portfolio = bucket.get("portfolio") or {}
    wid = str(portfolio.get("weather_sims_id") or "").strip()
    if wid:
        return wid
    for res in bucket.get("resources") or []:
        if not isinstance(res, dict):
            continue
        wid = str(res.get("weather_sims_id") or "").strip()
        if wid:
            return wid
    return None


def extended_init_has_rows(
    project_name: str,
    location: str,
    init: datetime,
    *,
    variable: str = DEFAULT_PROBE_VARIABLE,
    ensemble_path: int = PROBE_PATH,
) -> Optional[bool]:
    """Return True/False when probed; None if Forecast DB is unavailable."""
    project = (project_name or "").strip()
    loc = (location or "").strip()
    if not project or not loc:
        return None
    if init.tzinfo is None:
        init = init.replace(tzinfo=timezone.utc)
    else:
        init = init.astimezone(timezone.utc)

    sql = """
SELECT 1 FROM weather_forecast_ensemble_extended
WHERE project_name = %s AND location = %s AND variable = %s
  AND initialization = %s AND ensemble_path = %s
LIMIT 1
"""
    try:
        from data import forecast_db

        with forecast_db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '8000'")
                cur.execute(
                    sql,
                    (project, loc, variable, init, ensemble_path),
                )
                return cur.fetchone() is not None
    except RuntimeError:
        return None
    except Exception as exc:
        logger.warning(
            "Extended init probe failed for %s/%s@%s: %s",
            project,
            loc,
            init.isoformat(),
            exc,
        )
        return None


def resolve_weather_extended_init(
    short_init: datetime | str,
    *,
    project_name: str,
    location: str,
    variable: str = DEFAULT_PROBE_VARIABLE,
    max_lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> str:
    """Floor short init to UTC 6h grid; walk back until extended rows exist."""
    if isinstance(short_init, str):
        dt = datetime.fromisoformat(short_init.replace("Z", "+00:00"))
    else:
        dt = short_init
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    floored = floor_weather_long_init(dt)
    cadence = timedelta(hours=WEATHER_FORECAST_LONG_CADENCE_HOURS)
    max_steps = max(1, int(max_lookback_hours // WEATHER_FORECAST_LONG_CADENCE_HOURS))
    candidate = floored

    for step in range(max_steps + 1):
        has_rows = extended_init_has_rows(
            project_name,
            location,
            candidate,
            variable=variable,
        )
        if has_rows is True:
            if step > 0:
                logger.info(
                    "Extended init walk-back: %s/%s short=%s floor=%s -> landed=%s",
                    project_name,
                    location,
                    dt.isoformat(),
                    floored.isoformat(),
                    candidate.isoformat(),
                )
            return candidate.strftime("%Y-%m-%dT%H:%M:%SZ")
        if has_rows is None:
            break
        candidate -= cadence

    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")
