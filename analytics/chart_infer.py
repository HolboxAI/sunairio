"""Infer chart metadata for analytics confirm results."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

TIME_AXIS_COLUMNS = frozenset(
    {
        "valid_datetime",
        "hour_beginning",
        "sim_datetime",
        "local_hour",
        "local_date",
        "date",
    }
)

SKIP_Y_COLUMNS = frozenset(
    {
        "location",
        "location_name",
        "ensemble_path",
        "initialization",
        "project_name",
        "variable",
        "iso",
        "region",
        "zone",
        "load_zone",
    }
)

CORRELATION_SKIP_COLUMNS = frozenset(
    {
        *SKIP_Y_COLUMNS,
        *TIME_AXIS_COLUMNS,
        "hour",
        "n_points",
        "pearson_r",
        "spearman_r",
        "method",
        "variable_pair",
        "period_start",
        "period_end",
    }
)

CORRELATION_STAT_PREFIXES = ("avg_", "stddev_", "mean_", "min_", "max_", "pearson_", "spearman_")

PROBABILITY_COLUMN_RE = re.compile(
    r"(prob(ability)?|exceedance|pct|percent)",
    re.IGNORECASE,
)

DIAGNOSTIC_Y_COLUMNS = frozenset(
    {
        "n_paths",
        "paths_above",
        "total_paths",
        "n_joint_low",
        "n_dunkelflaute_paths",
        "threshold_mw",
        "threshold_value",
        "peak_mw",
        "peak_load_2023",
    }
)

SERIES_COLUMNS = (
    "location_name",
    "location",
    "region",
    "zone",
    "load_zone",
    "resource_name",
)


def infer_chart_from_rep(
    rep: Dict[str, Any],
    data: Optional[Dict[str, Any]],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Return (chart_applicable, chart_details, timezone) for a confirm result."""
    if not rep or not data:
        return False, None, _entity_timezone(rep)

    columns: List[str] = list(data.get("columns") or [])
    rows: List[Any] = list(data.get("rows") or [])
    if len(columns) < 2 or len(rows) < 2:
        return False, None, _entity_timezone(rep)

    analysis = str(rep.get("analysis_type") or "").lower()
    viz = rep.get("visualization") or {}
    viz_required = bool(viz.get("required") or viz.get("chart"))
    chart_type = str(viz.get("chart") or viz.get("chart_type") or "line").lower()

    if analysis == "correlation" and chart_type == "scatter":
        return _infer_correlation_scatter(rep, columns, rows)

    if analysis not in ("time_series", "comparison", "distribution", "correlation") and not viz_required:
        return False, None, _entity_timezone(rep)

    x_col = _pick_x_column(columns)
    if not x_col:
        return False, None, _entity_timezone(rep)

    series_col = _detect_series_column(columns, rows, x_col, rep)
    y_cols = _pick_y_columns(
        columns, rows, x_col, rep=rep, exclude={series_col} if series_col else None
    )
    if not y_cols:
        return False, None, _entity_timezone(rep)

    if chart_type not in ("line", "bar", "scatter"):
        chart_type = "line"
    if chart_type == "scatter" and analysis != "correlation":
        chart_type = "line"

    unit = str((rep.get("variable") or {}).get("unit") or viz.get("unit") or "")
    y_units_raw = viz.get("y_units") or []
    rep_vars = list(rep.get("variables") or [])
    if analysis == "probability":
        y_units = ["%"] * len(y_cols)
    elif len(rep_vars) >= len(y_cols) and not series_col:
        y_units = [str(v.get("unit") or "") for v in rep_vars[: len(y_cols)]]
    elif isinstance(y_units_raw, list) and len(y_units_raw) == len(y_cols):
        y_units = [str(u or "") for u in y_units_raw]
    elif unit:
        y_units = [unit] * len(y_cols)
    else:
        y_units = [""] * len(y_cols)

    details: Dict[str, Any] = {
        "chart_type": chart_type,
        "x_axis": [x_col],
        "y_axis": y_cols if not series_col else [y_cols[0]],
        "x_unit": [""],
        "y_unit": y_units if not series_col else [y_units[0] if y_units else unit],
        "display_columns": _display_columns(columns, x_col, y_cols, series_col),
    }
    if series_col:
        details["series_column"] = series_col
    if (
        chart_type != "scatter"
        and analysis != "probability"
        and _infer_dual_axis(viz, y_cols, y_units, rep, series_col)
    ):
        details["dual_axis"] = True
    return True, details, _entity_timezone(rep)


def _infer_correlation_scatter(
    rep: Dict[str, Any],
    columns: List[str],
    rows: List[Any],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """Scatter plot for correlation: x/y are paired variable columns, not time."""
    x_col, y_col, x_unit, y_unit = _pick_correlation_axes(rep, columns, rows)
    if not x_col or not y_col:
        return False, None, _entity_timezone(rep)

    details: Dict[str, Any] = {
        "chart_type": "scatter",
        "x_axis": [x_col],
        "y_axis": [y_col],
        "x_unit": [x_unit],
        "y_unit": [y_unit],
    }
    return True, details, _entity_timezone(rep)


def _pick_correlation_axes(
    rep: Dict[str, Any],
    columns: List[str],
    rows: List[Any],
) -> Tuple[Optional[str], Optional[str], str, str]:
    pair_cols = _correlation_value_columns(columns, rows)
    if len(pair_cols) < 2:
        return None, None, "", ""

    rep_vars = list(rep.get("variables") or [])
    var_names = [str(v.get("name") or "") for v in rep_vars if v.get("name")]
    if len(var_names) < 2:
        primary = str((rep.get("variable") or {}).get("name") or "")
        if primary:
            var_names = [primary]

    matched: List[str] = []
    for name in var_names:
        col = _match_column_for_variable(name, pair_cols)
        if col and col not in matched:
            matched.append(col)

    for col in pair_cols:
        if col not in matched:
            matched.append(col)

    if len(matched) < 2:
        return None, None, "", ""

    x_col, y_col = matched[0], matched[1]
    x_unit, y_unit = _units_for_columns(rep, x_col, y_col, var_names)
    return x_col, y_col, x_unit, y_unit


def _correlation_value_columns(columns: List[str], rows: List[Any]) -> List[str]:
    out: List[str] = []
    for col in columns:
        lower = col.lower()
        if lower in CORRELATION_SKIP_COLUMNS:
            continue
        if any(lower.startswith(prefix) for prefix in CORRELATION_STAT_PREFIXES):
            continue
        if _column_looks_numeric(rows, columns, col):
            out.append(col)
    return out


def _match_column_for_variable(var_name: str, columns: List[str]) -> Optional[str]:
    if not var_name:
        return None
    token = var_name.lower()
    for col in columns:
        if token in col.lower():
            return col
    return None


def _units_for_columns(
    rep: Dict[str, Any],
    x_col: str,
    y_col: str,
    var_names: List[str],
) -> Tuple[str, str]:
    rep_vars = list(rep.get("variables") or [])
    units_by_name: Dict[str, str] = {
        str(v.get("name") or "").lower(): str(v.get("unit") or "")
        for v in rep_vars
        if v.get("name")
    }

    def unit_for(col: str) -> str:
        lower = col.lower()
        for name, unit in units_by_name.items():
            if name and name in lower:
                return unit
        return ""

    x_unit = unit_for(x_col)
    y_unit = unit_for(y_col)
    if x_unit and y_unit:
        return x_unit, y_unit

    viz = rep.get("visualization") or {}
    y_units = viz.get("y_units") or []
    if isinstance(y_units, list) and len(y_units) >= 2:
        return str(y_units[0] or ""), str(y_units[1] or "")
    return x_unit, y_unit


def _infer_dual_axis(
    viz: Dict[str, Any],
    y_cols: List[str],
    y_units: List[str],
    rep: Dict[str, Any],
    series_col: Optional[str],
) -> bool:
    if series_col or len(y_cols) < 2:
        return False
    chart = str(viz.get("chart") or viz.get("chart_type") or "").lower()
    if chart == "scatter":
        return False
    if viz.get("dual_axis"):
        return True
    distinct_units = {u for u in y_units if u}
    if len(distinct_units) >= 2:
        return True
    rep_vars = rep.get("variables") or []
    if len(rep_vars) >= 2:
        var_units = {str(v.get("unit") or "") for v in rep_vars if v.get("unit")}
        if len(var_units) >= 2:
            return True
    return False


def _entity_timezone(rep: Optional[Dict[str, Any]]) -> Optional[str]:
    if not rep:
        return None
    tz = (rep.get("entity") or {}).get("timezone")
    return str(tz) if tz else None


def _pick_x_column(columns: List[str]) -> Optional[str]:
    for col in columns:
        if col.lower() in TIME_AXIS_COLUMNS:
            return col
    for col in columns:
        lower = col.lower()
        if any(token in lower for token in ("time", "hour", "date", "valid")):
            return col
    return columns[0] if columns else None


def _column_index(columns: List[str], name: str) -> int:
    lower_map = {c.lower(): i for i, c in enumerate(columns)}
    return lower_map.get(name.lower(), -1)


def _detect_series_column(
    columns: List[str],
    rows: List[Any],
    x_col: str,
    rep: Dict[str, Any],
) -> Optional[str]:
    """Detect long-format location/series column for multi-line charts."""
    loc_count = int((rep.get("locations") or {}).get("count") or 0)
    x_idx = _column_index(columns, x_col)
    if x_idx < 0:
        return None

    x_values = [_cell(row, x_idx) for row in rows]
    has_duplicate_x = len(x_values) != len(set(x_values))

    for candidate in SERIES_COLUMNS:
        idx = _column_index(columns, candidate)
        if idx < 0:
            continue
        series_values = [_cell(row, idx) for row in rows]
        distinct = {v for v in series_values if v not in (None, "")}
        if len(distinct) < 2:
            continue
        if has_duplicate_x or loc_count >= 2:
            return columns[idx]

    if loc_count >= 2 and has_duplicate_x:
        for col in columns:
            if col.lower() in SKIP_Y_COLUMNS or col.lower() == x_col.lower():
                continue
            idx = columns.index(col)
            if _column_looks_numeric(rows, columns, col):
                continue
            distinct = {_cell(row, idx) for row in rows}
            distinct.discard(None)
            distinct.discard("")
            if len(distinct) >= 2:
                return col
    return None


def _cell(row: Any, idx: int) -> Any:
    if isinstance(row, (list, tuple)):
        return row[idx] if idx < len(row) else None
    if isinstance(row, dict):
        return None
    return row


def _pick_y_columns(
    columns: List[str],
    rows: List[Any],
    x_col: str,
    *,
    rep: Optional[Dict[str, Any]] = None,
    exclude: Optional[set] = None,
) -> List[str]:
    skip = set(SKIP_Y_COLUMNS)
    skip.add(x_col.lower())
    if exclude:
        skip.update(c.lower() for c in exclude)

    analysis = str((rep or {}).get("analysis_type") or "").lower()
    if analysis == "probability":
        prob_cols = _pick_probability_columns(columns, rows, skip)
        if prob_cols:
            return prob_cols

    y_cols: List[str] = []
    for col in columns:
        if col.lower() in skip or _is_diagnostic_column(col):
            continue
        if _column_looks_numeric(rows, columns, col):
            y_cols.append(col)
    return y_cols


def _pick_probability_columns(
    columns: List[str],
    rows: List[Any],
    skip: set,
) -> List[str]:
    """Primary chart series for exceedance / probability queries."""
    matched: List[str] = []
    for col in columns:
        lower = col.lower()
        if lower in skip or _is_diagnostic_column(col):
            continue
        if not PROBABILITY_COLUMN_RE.search(lower):
            continue
        if _column_looks_numeric(rows, columns, col):
            matched.append(col)
    return matched


def _is_diagnostic_column(col: str) -> bool:
    lower = col.lower()
    if lower in DIAGNOSTIC_Y_COLUMNS:
        return True
    return lower.startswith(("threshold_", "peak_", "n_"))


def _display_columns(
    columns: List[str],
    x_col: str,
    y_cols: List[str],
    series_col: Optional[str],
) -> List[str]:
    """Columns to show in the table view alongside the chart."""
    ordered: List[str] = []
    for col in (x_col, series_col, *y_cols):
        if col and col in columns and col not in ordered:
            ordered.append(col)
    return ordered or list(columns)


def _column_looks_numeric(rows: List[Any], columns: List[str], col: str) -> bool:
    idx = columns.index(col)
    checked = 0
    numeric = 0
    for row in rows[:12]:
        val = row[idx] if isinstance(row, (list, tuple)) else row.get(col)
        if val is None or val == "":
            continue
        checked += 1
        if isinstance(val, (int, float)):
            numeric += 1
        else:
            try:
                float(str(val))
                numeric += 1
            except (TypeError, ValueError):
                return False
    return checked > 0 and numeric == checked
