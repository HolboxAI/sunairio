"""Backfill chart axis units from cached variables catalog."""

from __future__ import annotations

import re
from typing import List, Optional

from core.models import AgentEnvelope, ConversationState
from data import metadata_db

_VAR_EQ = re.compile(r"variable\s*=\s*'([^']+)'", re.I)
_VAR_IN = re.compile(r"variable\s+IN\s*\(([^)]+)\)", re.I)
_QUOTED = re.compile(r"'([^']+)'")
_TIME_COLUMNS = frozenset({"valid_datetime", "hour_beginning", "sim_datetime"})


def extract_variables_from_sql(sql: str) -> List[str]:
    if not sql:
        return []
    found: List[str] = []
    for m in _VAR_EQ.finditer(sql):
        found.append(m.group(1))
    for m in _VAR_IN.finditer(sql):
        for q in _QUOTED.findall(m.group(1)):
            found.append(q)
    seen = set()
    out: List[str] = []
    for v in found:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _unit_for_y_column(column: str, sql_vars: List[str], units_map: dict) -> str:
    if len(sql_vars) == 1:
        return units_map.get(sql_vars[0], "")
    col = column.lower()
    for var in sql_vars:
        if var.lower() in col:
            return units_map.get(var, "")
    if sql_vars:
        return units_map.get(sql_vars[0], "")
    return ""


def enrich_chart_units(
    envelope: AgentEnvelope,
    conversation_state: Optional[ConversationState] = None,
) -> AgentEnvelope:
    if not envelope.chart_applicable or not envelope.chart_details:
        return envelope
    units_map = metadata_db.get_variable_units()
    if not units_map:
        return envelope

    details = envelope.chart_details
    sql_vars = extract_variables_from_sql(envelope.answer or "")
    if not sql_vars and conversation_state and conversation_state.variable:
        sql_vars = [conversation_state.variable]

    while len(details.x_unit) < len(details.x_axis):
        details.x_unit.append("")
    for i, col in enumerate(details.x_axis):
        if details.x_unit[i]:
            continue
        if col.lower() in _TIME_COLUMNS:
            details.x_unit[i] = "UTC"

    while len(details.y_unit) < len(details.y_axis):
        details.y_unit.append("")
    for i, col in enumerate(details.y_axis):
        if details.y_unit[i]:
            continue
        details.y_unit[i] = _unit_for_y_column(col, sql_vars, units_map)

    return envelope
