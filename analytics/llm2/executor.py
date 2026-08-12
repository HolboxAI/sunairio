"""Analytics LLM2 executor — Metadata DB and Forecast DB only.

Lake / Glue and cross-database statements are explicitly unsupported in this
phase (placeholders for later work). This module does **not** import
``core.executor``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from analytics.llm2.parser import Llm2Plan
from data import forecast_db, metadata_db
from security.sql_guard import validate_sql

logger = logging.getLogger(__name__)

_LAKE_MARKER = re.compile(r"\bglue\s*\.", re.IGNORECASE)
_HISTORICAL_MARKER = re.compile(r"\bhistorical_iso_", re.IGNORECASE)
_FORECAST_MARKERS = (
    "weather_forecast_ensemble_short",
    "weather_forecast_ensemble_extended",
    "weather_seasonal_ensemble",
    "energy_forecast_ensemble",
    "energy_base_ensemble",
    "fundamental_price_forecast_ensemble",
    "fundamental_price_balmo_ensemble",
    "fundamental_price_base_ensemble",
)
_METADATA_CATALOG = (
    "entities",
    "locations",
    "resources",
    "resource_types",
    "variables",
    "location_variables",
    "resource_variables",
    "ensemble_runs",
    "user_entities",
    "markets",
)


class AnalyticsExecuteError(Exception):
    """Raised when analytics SQL cannot be executed in this phase."""


def classify_target(sql: str) -> str:
    """Return metadata | forecast | lake | cross | unknown."""
    text = sql or ""
    upper = text.upper()
    has_lake = bool(_LAKE_MARKER.search(text))
    has_hist = bool(_HISTORICAL_MARKER.search(text))
    has_forecast = any(m in text.lower() for m in _FORECAST_MARKERS)
    has_catalog = any(
        re.search(rf"\b{re.escape(t)}\b", text, re.IGNORECASE)
        for t in _METADATA_CATALOG
    )
    has_metadata = has_hist or has_catalog

    if has_lake:
        return "lake"
    if has_metadata and has_forecast:
        return "cross"
    if has_metadata:
        return "metadata"
    if has_forecast:
        return "forecast"
    # Fallback: trust nothing exotic
    if "SELECT" in upper:
        return "unknown"
    return "unknown"


def execute_plan(
    plan: Llm2Plan,
    *,
    request_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run LLM2 SQL. Returns (result_payload, execution_detail)."""
    if plan.target == "unsupported" or not plan.sql:
        raise AnalyticsExecuteError(
            "SQL generation marked this plan unsupported for Metadata/Forecast "
            "execution (Lake or cross-DB may be required)."
        )

    errors = []
    try:
        validate_sql(plan.sql)
    except ValueError as e:
        raise AnalyticsExecuteError(str(e)) from e

    inferred = classify_target(plan.sql)
    target = plan.target
    if inferred == "lake":
        # TODO(lake): route to lake_db / Flight SQL when enabled.
        raise AnalyticsExecuteError(
            "Lake/Glue SQL is not enabled in this analytics phase."
        )
    if inferred == "cross":
        # TODO(cross-db): split historical CTE + forecast bind like v1 later.
        raise AnalyticsExecuteError(
            "Cross-database SQL (Metadata + Forecast in one statement) is not "
            "enabled yet in analytics LLM2."
        )
    if inferred in ("metadata", "forecast") and inferred != target:
        logger.info(
            "LLM2 target=%s but SQL classifies as %s; using classified backend",
            target,
            inferred,
        )
        target = inferred
    if target not in ("metadata", "forecast"):
        raise AnalyticsExecuteError(
            f"Cannot route SQL to a supported backend (target={plan.target!r}, "
            f"classified={inferred!r})."
        )

    if target == "metadata":
        result = metadata_db.execute_query(plan.sql, request_id=request_id)
    else:
        result = forecast_db.execute_query(plan.sql, request_id=request_id)

    detail = {
        "backend": target,
        "classified": inferred,
        "row_count": result.get("row_count"),
        "truncated": result.get("truncated"),
        "query_time_ms": result.get("query_time_ms"),
        # Placeholders for later phases
        "lake": None,  # TODO(lake)
        "cross_db": None,  # TODO(cross-db)
    }
    return result, detail


def fill_result_template(
    template: Optional[str],
    result: Dict[str, Any],
) -> Optional[str]:
    """Substitute {alias} placeholders from the first result row."""
    if not template:
        return None
    columns: List[str] = list(result.get("columns") or [])
    rows = list(result.get("rows") or [])
    if not columns or not rows:
        return template
    row0 = rows[0]
    values: Dict[str, Any] = {}
    for i, col in enumerate(columns):
        if isinstance(row0, (list, tuple)):
            values[col] = row0[i] if i < len(row0) else None
        elif isinstance(row0, dict):
            values[col] = row0.get(col)
    out = template
    for col, val in values.items():
        for key in (col, col.lower(), col.upper()):
            out = out.replace("{" + key + "}", _fmt(val))
    return out


def preview_row_limit(row_count: int, *, cap_large: int = 48) -> int:
    """Show full hourly day/week results; cap only very large tables."""
    if row_count <= 168:
        return row_count
    return min(row_count, cap_large)


def format_answer_message(
    *,
    template_filled: Optional[str],
    result: Dict[str, Any],
    plan: Llm2Plan,
    max_preview_rows: Optional[int] = None,
) -> str:
    """User-facing assistant text for chat after execution."""
    parts: List[str] = []
    if template_filled:
        parts.append(template_filled)
    columns = list(result.get("columns") or [])
    rows = list(result.get("rows") or [])
    n = len(rows)
    limit = max_preview_rows if max_preview_rows is not None else preview_row_limit(n)
    if n == 0:
        parts.append("The query returned no rows.")
    elif n == 1 and template_filled:
        pass
    else:
        parts.append(_preview_table(columns, rows, limit))
        if n > limit:
            parts.append(f"Showing {limit} of {n} rows.")
    if plan.notes:
        parts.append("Notes: " + "; ".join(plan.notes))
    return "\n\n".join(p for p in parts if p).strip()


def _fmt(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        if abs(val - round(val)) < 1e-9:
            return f"{int(round(val)):,}"
        return f"{val:,.2f}"
    return str(val)


def _preview_table(
    columns: List[str], rows: List[Any], limit: int
) -> str:
    if not columns:
        return ""
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, sep]
    for row in rows[:limit]:
        cells = []
        for i, _col in enumerate(columns):
            if isinstance(row, (list, tuple)):
                cells.append(_fmt(row[i] if i < len(row) else None))
            elif isinstance(row, dict):
                cells.append(_fmt(row.get(_col)))
            else:
                cells.append(_fmt(row))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)
