"""Bind v3 chart axis units from the user question and final SQL.

The plotted numbers are the SELECT expressions. Catalog ``variable_units`` is
only a fallback when the series is that variable's native value (e.g. P50 load
→ MW). Probability, slopes, and converted temperatures must not inherit the
filtered variable's unit. Conversation-state leftovers are never used.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from core.chart_units import extract_variables_from_sql
from core.models import ChartDetails
from planner.models import PlannerEnvelope

_TIME_COLUMNS = frozenset({
    "valid_datetime",
    "hour_beginning",
    "sim_datetime",
    "local_hour",
    "local_date",
    "date",
})

_AS_ALIAS = re.compile(r"\bAS\s+(\"?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$", re.I)
_IDENT = re.compile(r"(\"?)([A-Za-z_][A-Za-z0-9_]*)\1\s*$")
_PROB_ALIAS = re.compile(
    r"(probab|exceedance|likelihood|frac_path|path_frac)",
    re.I,
)
_PCT_ALIAS = re.compile(r"(percent|_pct$|_pct_|pct_)", re.I)
_SLOPE_ALIAS = re.compile(r"(per_degree|mw_per|sensitivity|regr_slope|slope)", re.I)
_FAHRENHEIT = re.compile(r"(fahrenheit|\bdeg(?:ree)?s?\s*f\b|°\s*f|\b_f\b)", re.I)
_COUNT_1000 = re.compile(
    r"count\s*\(\s*\*\s*\)|/\s*1000(?:\.0)?|::float\s*/\s*1000",
    re.I,
)


def _var_mentioned(variable: str, alias: str) -> bool:
    al = (alias or "").lower()
    vl = (variable or "").lower()
    if not vl or not al:
        return False
    if vl in al:
        return True
    stem = vl.split("_")[0]
    return len(stem) >= 3 and stem in al


def _outer_select_list(sql: str) -> str:
    text = sql or ""
    depth = 0
    last_select = -1
    i = 0
    upper = text.upper()
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and upper.startswith("SELECT", i) and (
            i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")
        ):
            last_select = i
        i += 1
    if last_select < 0:
        return ""
    rest = text[last_select + 6 :]
    depth = 0
    for j, ch in enumerate(rest):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and rest[j : j + 4].upper() == "FROM" and (
            j == 0 or not (rest[j - 1].isalnum() or rest[j - 1] == "_")
        ):
            return rest[:j].strip()
    return rest.strip()


def _split_select_items(select_list: str) -> List[str]:
    items: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in select_list:
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                items.append(item)
            buf = []
        else:
            buf.append(ch)
    item = "".join(buf).strip()
    if item:
        items.append(item)
    return items


def parse_select_aliases(sql: str) -> Dict[str, str]:
    """Map SELECT alias (lowercase) → expression text."""
    mapping: Dict[str, str] = {}
    for item in _split_select_items(_outer_select_list(sql)):
        as_match = _AS_ALIAS.search(item)
        if as_match:
            alias = as_match.group(2)
            expr = item[: as_match.start()].strip()
        else:
            ident = _IDENT.search(item.strip())
            if not ident:
                continue
            alias = ident.group(2)
            expr = item.strip()
        mapping[alias.lower()] = expr
    return mapping


def _catalog_unit(
    variable: str,
    expr: str,
    alias: str,
    units_map: Dict[str, str],
    question: str,
) -> str:
    unit = str(units_map.get(variable) or "")
    blob = f"{alias} {expr} {question}"
    if "temp" in variable.lower() or unit in {"°C", "C", "degC"}:
        if _FAHRENHEIT.search(alias) or _FAHRENHEIT.search(expr):
            return "°F"
        if _FAHRENHEIT.search(question) and re.search(
            r"\*\s*9\s*/\s*5|\*\s*1\.8|32", expr
        ):
            return "°F"
    return unit


def _unit_for_series(
    alias: str,
    expr: str,
    sql_vars: List[str],
    units_map: Dict[str, str],
    question: str,
) -> str:
    combined = f"{alias} {expr}"
    if _COUNT_1000.search(expr) or _PROB_ALIAS.search(alias):
        if _PCT_ALIAS.search(alias) or re.search(r"\*\s*100", expr):
            return "%"
        return "probability"
    if _SLOPE_ALIAS.search(combined):
        y_var = next((v for v in sql_vars if "temp" not in v.lower()), sql_vars[0] if sql_vars else "")
        x_var = next((v for v in sql_vars if "temp" in v.lower()), "")
        y_u = units_map.get(y_var, "MW") if y_var else "MW"
        x_u = units_map.get(x_var, "°C") if x_var else "°C"
        if _FAHRENHEIT.search(combined) or _FAHRENHEIT.search(question):
            if _FAHRENHEIT.search(expr) or _FAHRENHEIT.search(alias):
                x_u = "°F"
        return f"{y_u}/{x_u}"

    for var in sorted(sql_vars, key=len, reverse=True):
        if _var_mentioned(var, alias):
            return _catalog_unit(var, expr, alias, units_map, question)

    for var in sorted(units_map.keys(), key=len, reverse=True):
        if len(var) > 2 and var.lower() in alias.lower():
            return _catalog_unit(var, expr, alias, units_map, question)

    if len(sql_vars) == 1:
        return _catalog_unit(sql_vars[0], expr, alias, units_map, question)
    return ""


def bind_chart_units(
    envelope: PlannerEnvelope,
    *,
    user_question: str = "",
    timezone: Optional[str] = None,
    units_map: Optional[Dict[str, str]] = None,
) -> PlannerEnvelope:
    """Overwrite chart x_unit/y_unit from final_sql + question. No-op if no chart."""
    if not envelope.chart_applicable or not envelope.chart_details:
        return envelope
    details: ChartDetails = envelope.chart_details
    sql = envelope.final_sql or ""
    aliases = parse_select_aliases(sql)
    sql_vars = extract_variables_from_sql(sql)
    catalog = units_map or {}
    question = user_question or envelope.question or ""

    x_unit: List[str] = []
    for col in details.x_axis:
        if col.lower() in _TIME_COLUMNS:
            x_unit.append(timezone or "UTC")
        else:
            expr = aliases.get(col.lower(), "")
            x_unit.append(_unit_for_series(col, expr, sql_vars, catalog, question))
    details.x_unit = x_unit

    y_unit: List[str] = []
    for col in details.y_axis:
        expr = aliases.get(col.lower(), "")
        y_unit.append(_unit_for_series(col, expr, sql_vars, catalog, question))
    details.y_unit = y_unit
    envelope.chart_details = details
    return envelope
