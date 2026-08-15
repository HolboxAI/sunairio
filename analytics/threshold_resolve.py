"""Resolve symbolic or historical-source probability thresholds before LLM2."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any, Dict, Optional, Tuple

from data import metadata_db

logger = logging.getLogger(__name__)

_AGG_OPS = {
    "max": "MAX",
    "min": "MIN",
    "mean": "AVG",
    "avg": "AVG",
    "average": "AVG",
}


def needs_historical_threshold_resolution(rep: Dict[str, Any]) -> bool:
    stats = rep.get("statistics") or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    if _historical_source_spec(params) is None:
        return False
    return not _is_numeric(params.get("threshold"))


def resolve_historical_threshold(
    rep: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[float]]:
    """Fetch a historical threshold and inject it into the REP for forecast SQL."""
    stats = rep.get("statistics") or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    if not needs_historical_threshold_resolution(rep):
        return rep, _numeric_or_none(params.get("threshold"))

    spec = _historical_source_spec(params) or {}
    iso, region = _iso_region(rep, params, spec)
    variable = str(
        spec.get("variable")
        or params.get("threshold_variable")
        or (rep.get("variable") or {}).get("name")
        or ""
    ).strip()
    operation = str(
        spec.get("aggregation")
        or spec.get("statistic")
        or params.get("threshold_statistic")
        or "max"
    ).strip().lower()
    sql_agg = _AGG_OPS.get(operation)
    if not sql_agg or not iso or not region or not variable:
        raise ValueError(
            "Could not resolve historical threshold: missing entity, location, variable, or statistic."
        )

    start, end = _period_bounds_from_spec(spec, params)
    if not start or not end:
        raise ValueError(
            "Could not resolve historical threshold: threshold period must be a calendar year or explicit dates."
        )

    sql = f"""
        SELECT {sql_agg}(hour_value) AS threshold_value
        FROM historical_iso_load_gen
        WHERE iso = %(iso)s
          AND region = %(region)s
          AND variable = %(variable)s
          AND hour_beginning >= %(start)s::timestamp
          AND hour_beginning < (%(end)s::date + INTERVAL '1 day')
    """
    payload = metadata_db.execute_query(
        sql,
        params={"iso": iso, "region": region, "variable": variable, "start": start, "end": end},
        request_id=request_id,
    )
    rows = payload.get("rows") or []
    if not rows:
        raise ValueError(
            f"No historical actuals found for {iso} {variable} over {start} to {end}."
        )
    raw = rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0].get("threshold_value")
    if raw is None:
        raise ValueError("Historical threshold query returned NULL.")
    threshold = float(raw)

    patched = copy.deepcopy(rep)
    patched_stats = dict(patched.get("statistics") or {})
    patched_params = dict(patched_stats.get("parameters") or {})
    patched_params["threshold"] = threshold
    patched_params["threshold_resolved_from"] = {
        "source": "historical",
        "iso": iso,
        "region": region,
        "variable": variable,
        "operation": operation,
        "start": start,
        "end": end,
    }
    patched_stats["parameters"] = patched_params
    patched["statistics"] = patched_stats
    notes = list(patched.get("notes") or [])
    notes.append(
        f"Threshold resolved from observed {operation} {variable} for {iso} "
        f"({start[:4]}): {threshold:,.2f} MW."
    )
    patched["notes"] = notes
    logger.info(
        "Resolved historical threshold for %s: %s=%s (%s–%s)",
        iso,
        operation,
        threshold,
        start,
        end,
    )
    return patched, threshold


def _historical_source_spec(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    src = params.get("threshold_source")
    if src == "historical":
        return params
    if isinstance(src, dict):
        intent = str(src.get("intent") or src.get("source") or "").strip().lower()
        if intent == "historical":
            return src
    return None


def _iso_region(
    rep: Dict[str, Any],
    params: Dict[str, Any],
    spec: Dict[str, Any],
) -> Tuple[str, str]:
    entity = rep.get("entity") or {}
    iso = str(
        spec.get("entity")
        or params.get("threshold_entity")
        or entity.get("display_name")
        or entity.get("name")
        or ""
    ).strip()
    locs = list((rep.get("locations") or {}).get("values") or [])
    region = ""
    if locs:
        region = str(locs[0].get("energy_sims_id") or "").strip()
    if not region:
        region = iso.lower()
    return iso, region


def _period_bounds_from_spec(
    spec: Dict[str, Any],
    params: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    timeframe = spec.get("timeframe")
    if isinstance(timeframe, dict):
        start = _as_date_str(timeframe.get("start"))
        end = _as_date_str(timeframe.get("end"))
        if start and end:
            return start, end
    return _period_bounds(str(params.get("threshold_period") or spec.get("period") or ""))


def _period_bounds(period: str) -> Tuple[Optional[str], Optional[str]]:
    text = (period or "").strip()
    m = re.match(r"^(20\d{2})$", text)
    if not m:
        m = re.search(r"(?<!\d)(20\d{2})(?!\d)", text)
    if not m:
        return None, None
    year = m.group(1)
    return f"{year}-01-01", f"{year}-12-31"


def _as_date_str(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _is_numeric(raw: Any) -> bool:
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)):
        return True
    text = str(raw).strip().replace(",", "")
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def _numeric_or_none(raw: Any) -> Optional[float]:
    if not _is_numeric(raw):
        return None
    return float(str(raw).strip().replace(",", ""))
