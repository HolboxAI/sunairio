"""Answer fully-bound historical scalar plans from Metadata DB actuals.

Like ``metadata_answer`` for catalogs: when the resolver has already bound
entity / location / variable / timeframe and the ask is a single max|min|mean
over energy actuals, fetch the number and return it. Anything else falls
through to the normal confirm path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

from analytics.models import (
    ResolvedEntity,
    ResolvedExecutionPlan,
    ResolvedInitialization,
    ResolvedLocations,
    ResolvedTimeframe,
    ResolvedVariable,
)
from data import metadata_db

logger = logging.getLogger(__name__)

_AGG_OPS = {
    "max": "MAX",
    "min": "MIN",
    "mean": "AVG",
    "avg": "AVG",
    "average": "AVG",
}

# User is asking what the unresolved / symbolic threshold actually is.
_THRESHOLD_VALUE_ASK = re.compile(
    r"(?:"
    r"what(?:'s|\s+is|\s+was)|tell\s+me|how\s+(?:did|do|was|is)|"
    r"where\s+(?:did|do)|which"
    r").{0,60}?"
    r"(?:peak|threshold|max(?:imum)?|min(?:imum)?|value|number|_mw|annual_peak)|"
    r"(?:peak|threshold|max(?:imum)?).{0,40}?(?:value|number|mw|figure)|"
    r"how\s+did\s+you\s+(?:calculate|comput|get|derive|find)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HistoricalScalarResult:
    value: float
    operation: str
    sql_agg: str
    iso: str
    region: str
    variable: str
    unit: str
    start: str
    end: str
    location_label: str
    entity_label: str
    variable_label: str
    ref_key: str


def is_eligible(rep: ResolvedExecutionPlan) -> bool:
    """True only for a single unbound historical max/min/mean (no series/groupby).

    Richer plans (groupby month, charts, argmin timestamps, multi-series) must
    go through confirm → LLM2 SQL instead of this shortcut.
    """
    if (rep.intent or "").lower() != "historical":
        return False
    if (rep.analysis_type or "").lower() != "scalar":
        return False
    if not (rep.routing or {}).get("historical_database"):
        return False
    if "historical_iso_load_gen" not in (rep.required_schema or []):
        return False
    # Weather actuals are not in-platform yet.
    if (rep.variable.category or "").lower() == "weather":
        return False
    op = _operation(rep)
    if op not in _AGG_OPS:
        return False
    stats = rep.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    # groupby / series / viz → open SQL path
    if params.get("groupby") or params.get("group_by") or params.get("group"):
        return False
    viz = rep.visualization or {}
    if viz.get("required") or viz.get("chart"):
        return False
    if not rep.timeframe or not rep.timeframe.start or not rep.timeframe.end:
        return False
    if not rep.entity or not (rep.entity.display_name or "").strip():
        return False
    if not rep.variable or not (rep.variable.name or "").strip():
        return False
    locs = list((rep.locations.values if rep.locations else None) or [])
    if len(locs) != 1:
        return False
    if not (locs[0].get("energy_sims_id") or "").strip():
        return False
    if (rep.comparison or {}).get("enabled"):
        return False
    return True


def try_answer(rep: ResolvedExecutionPlan) -> Optional[Tuple[str, HistoricalScalarResult]]:
    """Fetch and format a scalar answer, or None to keep the confirm path."""
    if not is_eligible(rep):
        return None
    try:
        result = fetch_scalar(rep)
    except Exception as e:
        logger.warning("Historical scalar fetch failed; falling back to confirm: %s", e)
        return None
    if result is None:
        return None
    return format_answer(result), result


def fetch_scalar(rep: ResolvedExecutionPlan) -> Optional[HistoricalScalarResult]:
    op = _operation(rep)
    sql_agg = _AGG_OPS[op]
    loc = rep.locations.values[0]
    iso = (rep.entity.display_name or "").strip()
    region = (loc.get("energy_sims_id") or "").strip()
    variable = (rep.variable.name or "").strip()
    start = _as_date_str(rep.timeframe.start)
    end = _as_date_str(rep.timeframe.end)
    if not start or not end:
        return None

    sql = f"""
        SELECT {sql_agg}(hour_value) AS scalar_value
        FROM historical_iso_load_gen
        WHERE iso = %(iso)s
          AND region = %(region)s
          AND variable = %(variable)s
          AND hour_beginning >= %(start)s::timestamp
          AND hour_beginning < (%(end)s::date + INTERVAL '1 day')
    """
    params = {
        "iso": iso,
        "region": region,
        "variable": variable,
        "start": start,
        "end": end,
    }
    payload = metadata_db.execute_query(sql, params=params)
    rows = payload.get("rows") or []
    if not rows:
        return None
    raw = rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0].get("scalar_value")
    if raw is None:
        return None
    value = float(raw)
    location_label = (
        (rep.locations.label or "").strip()
        or (loc.get("location_name") or "").strip()
        or region
    )
    return HistoricalScalarResult(
        value=value,
        operation=op,
        sql_agg=sql_agg,
        iso=iso,
        region=region,
        variable=variable,
        unit=(rep.variable.unit or "").strip(),
        start=start,
        end=end,
        location_label=location_label,
        entity_label=(rep.entity.display_name or "").strip(),
        variable_label=(rep.variable.display_name or rep.variable.name or "").strip(),
        ref_key=_ref_key(iso, region, variable, op, start, end),
    )


def format_answer(result: HistoricalScalarResult) -> str:
    op_label = {
        "max": "peak (maximum)",
        "min": "minimum",
        "mean": "average",
        "avg": "average",
        "average": "average",
    }.get(result.operation, result.operation)
    unit = f" {result.unit}" if result.unit else ""
    period = _period_label(result.start, result.end)
    return (
        f"The {op_label} {result.variable_label} for **{result.entity_label}** "
        f"at **{result.location_label}** over {period} was "
        f"**{_format_number(result.value)}{unit}**.\n\n"
        "I can use this as the threshold for the follow-up probability analysis "
        "whenever you're ready."
    )


def result_to_ref(result: HistoricalScalarResult) -> Dict[str, Any]:
    return {
        "key": result.ref_key,
        "kind": "historical_scalar",
        "value": result.value,
        "unit": result.unit,
        "operation": result.operation,
        "entity": result.entity_label,
        "iso": result.iso,
        "region": result.region,
        "location_label": result.location_label,
        "variable": result.variable,
        "variable_label": result.variable_label,
        "start": result.start,
        "end": result.end,
    }


def looks_like_threshold_value_question(message: str) -> bool:
    return bool(_THRESHOLD_VALUE_ASK.search(message or ""))


def try_answer_threshold_followup(
    message: str,
    pending: Optional[Dict[str, Any]],
) -> Optional[Tuple[str, HistoricalScalarResult, ResolvedExecutionPlan]]:
    """If the user asks what a symbolic pending threshold is, fetch it from actuals.

    Safety net against LLM1 answering under ``awareness`` with invented MW figures.
    """
    if not pending or not looks_like_threshold_value_question(message):
        return None
    pending_rep = pending.get("rep") if isinstance(pending.get("rep"), dict) else None
    if not pending_rep:
        return None
    hist_rep = historical_scalar_rep_from_pending(pending_rep)
    if hist_rep is None:
        return None
    answered = try_answer(hist_rep)
    if answered is None:
        return None
    text, result = answered
    return text, result, hist_rep


def historical_scalar_rep_from_pending(
    pending_rep: Dict[str, Any],
) -> Optional[ResolvedExecutionPlan]:
    """Build a historical scalar REP from a forecast plan with a non-numeric threshold."""
    stats = pending_rep.get("statistics") or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    threshold = params.get("threshold")
    if threshold is None or _is_number(threshold):
        return None

    blob = " ".join(
        [
            str(threshold),
            " ".join(str(n) for n in (pending_rep.get("notes") or [])),
        ]
    )
    year = _year_from_text(blob)
    if not year:
        return None

    low = str(threshold).lower()
    if any(tok in low for tok in ("min", "trough", "lowest", "minimum")):
        operation = "min"
    elif any(tok in low for tok in ("mean", "average", "avg")):
        operation = "mean"
    else:
        operation = "max"

    entity_raw = pending_rep.get("entity") or {}
    loc_raw = pending_rep.get("locations") or {}
    var_raw = pending_rep.get("variable") or {}
    loc_values = list(loc_raw.get("values") or [])
    if not loc_values or not (loc_values[0].get("energy_sims_id") or "").strip():
        return None
    if not (entity_raw.get("display_name") or entity_raw.get("name") or "").strip():
        return None
    if not (var_raw.get("name") or "").strip():
        return None

    start = f"{year}-01-01"
    end = f"{year}-12-31"
    return ResolvedExecutionPlan(
        intent="historical",
        analysis_type="scalar",
        entity=ResolvedEntity(
            id=str(entity_raw.get("id") or ""),
            name=str(entity_raw.get("name") or ""),
            display_name=str(
                entity_raw.get("display_name") or entity_raw.get("name") or ""
            ),
            timezone=str(entity_raw.get("timezone") or "UTC"),
        ),
        locations=ResolvedLocations(
            mode=str(loc_raw.get("mode") or "explicit"),
            count=int(loc_raw.get("count") or len(loc_values)),
            values=loc_values,
            label=str(loc_raw.get("label") or ""),
        ),
        variable=ResolvedVariable(
            name=str(var_raw.get("name") or ""),
            display_name=str(var_raw.get("display_name") or var_raw.get("name") or ""),
            unit=str(var_raw.get("unit") or ""),
            category=str(var_raw.get("category") or "Energy"),
        ),
        timeframe=ResolvedTimeframe(start=start, end=end, mode="explicit"),
        initialization=ResolvedInitialization(mode="none", label="N/A"),
        statistics={"operation": operation, "parameters": {}, "value": None},
        routing={
            "forecast_database": False,
            "historical_database": True,
            "forecast_evolution": False,
            "metadata": False,
        },
        required_schema=["variables", "locations", "historical_iso_load_gen"],
        visualization={
            "required": False,
            "chart": None,
            "x": "Time",
            "y": "",
            "legend": None,
            "unit": str(var_raw.get("unit") or ""),
        },
        comparison={"enabled": False, "dimensions": []},
        notes=[
            f"Resolved symbolic threshold {threshold!r} via historical actuals lookup."
        ],
    )


def _is_number(raw: Any) -> bool:
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


def _year_from_text(text: str) -> Optional[str]:
    # Underscores are word chars, so \b fails inside 2023_annual_peak_*.
    m = re.search(r"(?<!\d)(20\d{2})(?!\d)", text or "")
    return m.group(1) if m else None


def _operation(rep: ResolvedExecutionPlan) -> str:
    stats = rep.statistics or {}
    return str(stats.get("operation") or "").strip().lower()


def _as_date_str(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw).strip()
    if not text:
        return None
    # Accept "2023-01-01" or ISO datetimes; keep the calendar date only.
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def _period_label(start: str, end: str) -> str:
    if start == end:
        return start
    if start.endswith("-01-01") and end.endswith("-12-31") and start[:4] == end[:4]:
        return start[:4]
    return f"{start} → {end}"


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def _ref_key(
    iso: str, region: str, variable: str, operation: str, start: str, end: str
) -> str:
    def slug(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")

    return (
        f"{slug(iso)}_{slug(region)}_{slug(variable)}_{slug(operation)}_"
        f"{slug(start)}_{slug(end)}"
    )
