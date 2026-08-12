"""Extract and apply multi-row session references from query results (Workstream 3)."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from analytics.models import AnalyticalExecutionPlan

_PER_LOC_THRESHOLD_PATTERNS = (
    r"each location",
    r"respective",
    r"their own",
    r"each of these",
    r"you just calculated",
    r"you just computed",
    r"from the (?:last|previous) (?:result|query|table)",
    r"those threshold",
    r"these threshold",
)

_VALUE_COLUMN_HINTS = {
    "peak_daily_total_mwh": "daily_total_mwh",
    "peak_daily_total": "daily_total_mwh",
    "daily_total_mwh": "daily_total_mwh",
    "peak_load_mw": "peak_hourly_mw",
    "max_load_mw": "peak_hourly_mw",
    "peak_mw": "peak_hourly_mw",
    "scalar_value": "peak_hourly_mw",
}

_LOCATION_COLUMN_HINTS = (
    "location_id",
    "location",
    "region",
    "energy_sims_id",
)

_NAME_COLUMN_HINTS = (
    "location_name",
    "location_label",
    "name",
)


def extract_from_result(
    result_payload: Dict[str, Any],
    *,
    entity: str = "",
    period: str = "",
) -> Optional[Dict[str, Any]]:
    """Build a location_threshold_table ref from tabular query output."""
    columns = [str(c) for c in (result_payload.get("columns") or [])]
    rows = list(result_payload.get("rows") or [])
    if not columns or not rows:
        return None

    col_lower = {c: c.lower() for c in columns}
    value_col = _find_value_column(col_lower)
    if not value_col:
        return None
    metric = _VALUE_COLUMN_HINTS.get(col_lower[value_col], "peak_hourly_mw")
    loc_col = _find_location_column(col_lower)
    name_col = _find_name_column(col_lower)

    out_rows: List[Dict[str, Any]] = []
    for raw in rows:
        row = _row_to_dict(columns, raw)
        loc_id = _cell(row, loc_col) or _cell(row, name_col)
        if not loc_id:
            continue
        val = _numeric(_cell(row, value_col))
        if val is None:
            continue
        entry: Dict[str, Any] = {
            "location_id": str(loc_id).strip().lower(),
            "location_name": str(_cell(row, name_col) or loc_id).strip(),
            "value": val,
            "unit": "MWh" if metric == "daily_total_mwh" else "MW",
        }
        for date_col in ("peak_date", "date", "peak_day"):
            if date_col in col_lower.values() or date_col in row:
                dv = _cell(row, date_col) or row.get(date_col)
                if dv:
                    entry["peak_date"] = str(dv)[:10]
                    break
        out_rows.append(entry)

    if len(out_rows) < 1:
        return None

    key_parts = [
        re.sub(r"[^a-z0-9]+", "_", (entity or "entity").lower()).strip("_"),
        re.sub(r"[^a-z0-9]+", "_", (period or "session").lower()).strip("_"),
        metric,
    ]
    return {
        "key": "_".join(p for p in key_parts if p),
        "kind": "location_threshold_table",
        "metric": metric,
        "entity": entity or "",
        "period": period or "",
        "rows": out_rows,
    }


def apply_session_thresholds(
    aep: AnalyticalExecutionPlan,
    refs: List[Dict[str, Any]],
    message: str,
) -> AnalyticalExecutionPlan:
    """Patch AEP statistics when user references prior per-location thresholds."""
    if not _wants_per_location_thresholds(message):
        return aep
    table = latest_location_threshold_table(refs)
    if not table:
        return aep

    aep = copy.deepcopy(aep)
    params = dict(aep.query.statistics.parameters or {})
    thresholds = {
        str(r.get("location_id") or ""): float(r["value"])
        for r in (table.get("rows") or [])
        if r.get("location_id") is not None and r.get("value") is not None
    }
    if not thresholds:
        return aep

    params["thresholds"] = thresholds
    params["threshold_mode"] = "per_location"
    metric = str(table.get("metric") or "")
    if metric == "daily_total_mwh":
        params["aggregation"] = "daily_sum"
        params["compare_metric"] = "daily_total_mwh"
    elif metric == "peak_hourly_mw":
        params["aggregation"] = "hourly"
        params["compare_metric"] = "peak_hourly_mw"

    aep.query.statistics.parameters = params
    if (aep.query.intent or "").lower() in ("", "forecast"):
        aep.query.intent = "forecast"
    if not aep.query.analysis_type:
        aep.query.analysis_type = "probability"
    return aep


def latest_location_threshold_table(
    refs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for ref in refs or []:
        if isinstance(ref, dict) and ref.get("kind") == "location_threshold_table":
            rows = ref.get("rows")
            if isinstance(rows, list) and rows:
                return ref
    return None


def infer_period_from_timeframe(start: str, end: str) -> str:
    s = (start or "")[:4]
    e = (end or "")[:4]
    if s and e and s == e:
        return s
    if s and e:
        return f"{s}_{e}"
    return "session"


def _wants_per_location_thresholds(message: str) -> bool:
    msg = (message or "").lower()
    return any(re.search(p, msg) for p in _PER_LOC_THRESHOLD_PATTERNS)


def _find_value_column(col_lower: Dict[str, str]) -> Optional[str]:
    for col, lower in col_lower.items():
        if lower in _VALUE_COLUMN_HINTS:
            return col
    for col, lower in col_lower.items():
        if any(h in lower for h in ("peak", "load", "total", "mwh", "mw", "scalar")):
            if "date" not in lower and "hour" not in lower:
                return col
    return None


def _find_location_column(col_lower: Dict[str, str]) -> Optional[str]:
    for hint in _LOCATION_COLUMN_HINTS:
        for col, lower in col_lower.items():
            if lower == hint or lower.endswith(hint):
                return col
    for col, lower in col_lower.items():
        if "location" in lower or lower in ("region", "pjm", "mida", "south", "west"):
            return col
    return None


def _find_name_column(col_lower: Dict[str, str]) -> Optional[str]:
    for hint in _NAME_COLUMN_HINTS:
        for col, lower in col_lower.items():
            if lower == hint:
                return col
    return None


def _row_to_dict(columns: List[str], raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (list, tuple)):
        return {columns[i]: raw[i] if i < len(raw) else None for i in range(len(columns))}
    return {}


def _cell(row: Dict[str, Any], col: Optional[str]) -> Any:
    if not col:
        return None
    if col in row:
        return row[col]
    lower = col.lower()
    for k, v in row.items():
        if str(k).lower() == lower:
            return v
    return None


def _numeric(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
