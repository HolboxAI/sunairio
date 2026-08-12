"""Proactive clarification for ambiguous analytical asks (Workstream 2)."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from analytics.models import AnalyticalExecutionPlan

_PEAK_DATE_PATTERNS = (
    r"which day",
    r"what day",
    r"when was",
    r"peak date",
    r"what date",
    r"which date",
    r"days were these",
    r"day was this",
    r"date was",
)

_MAX_IN_DAY_PATTERNS = (
    r"max.{0,20}load.{0,20}day",
    r"max.{0,20}in a day",
    r"max.{0,20}that day",
    r"maximum.{0,20}day",
)

_VAGUE_THRESHOLD_PATTERNS = (
    r"this threshold",
    r"that threshold",
    r"crossing this",
    r"crossing that",
    r"the threshold",
    r"these threshold",
)

_PER_LOCATION_PATTERNS = (
    r"each location",
    r"respective",
    r"their own",
    r"each of these",
    r"all of them",
    r"every location",
)


def detect_clarification_resolution(message: str) -> Dict[str, str]:
    """Persist user choices from clarify replies into session slots."""
    msg = (message or "").lower()
    slots: Dict[str, str] = {}
    if any(
        k in msg
        for k in (
            "daily total",
            "sum of 24",
            "total energy",
            "sum of the 24",
            "mwh",
            "the daily total",
        )
    ):
        slots["peak_metric"] = "daily_total_mwh"
    if any(
        k in msg
        for k in (
            "peak hour",
            "peak hourly",
            "single highest mw",
            "highest mw reading",
            "hourly peak",
            "peak hour's calendar date",
        )
    ):
        slots["peak_metric"] = "peak_hourly_mw"
    if "realtime" in msg.replace("-", "") or ("real" in msg and "time" in msg):
        slots["price_type"] = "real_time LMP"
    if "day ahead" in msg or "day-ahead" in msg or "da lmp" in msg:
        slots["price_type"] = "day_ahead LMP"
    return slots


def apply_resolved_slots(
    aep: AnalyticalExecutionPlan,
    session_slots: Dict[str, str],
) -> AnalyticalExecutionPlan:
    """Patch AEP statistics from persisted session slot choices."""
    if not session_slots:
        return aep
    aep = copy.deepcopy(aep)
    params = dict(aep.query.statistics.parameters or {})
    peak_metric = session_slots.get("peak_metric")
    if peak_metric == "daily_total_mwh":
        params["aggregation"] = "daily_sum"
        params["peak_metric"] = "daily_total_mwh"
    elif peak_metric == "peak_hourly_mw":
        params["aggregation"] = "hourly_peak"
        params["peak_metric"] = "peak_hourly_mw"
    price_type = session_slots.get("price_type")
    if price_type:
        params["price_type"] = price_type
    aep.query.statistics.parameters = params
    return aep


def check_ambiguity(
    message: str,
    aep: AnalyticalExecutionPlan,
    *,
    refs: Optional[List[Dict[str, Any]]] = None,
    session_slots: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Return a clarify message when the ask is underspecified, else None."""
    msg = (message or "").lower()
    refs = list(refs or [])
    slots = dict(session_slots or {})
    intent = (aep.query.intent or "").lower().replace(" ", "_")
    stats = aep.query.statistics
    op = str(stats.operation or stats.type or "").lower()
    params = stats.parameters if isinstance(stats.parameters, dict) else {}
    peak_metric = slots.get("peak_metric") or params.get("peak_metric")

    if intent in ("historical", "history"):
        asks_date = any(re.search(p, msg) for p in _PEAK_DATE_PATTERNS)
        asks_max_in_day = any(re.search(p, msg) for p in _MAX_IN_DAY_PATTERNS)
        has_daily = peak_metric == "daily_total_mwh" or _norm(params.get("aggregation")) == "daily_sum"
        has_hourly = peak_metric == "peak_hourly_mw" or _norm(params.get("aggregation")) == "hourly_peak"

        if asks_max_in_day and not has_daily and not has_hourly:
            if "daily total" not in msg and "peak hour" not in msg:
                return _MAX_IN_DAY_CLARIFY

        if asks_date and op in ("argmax", "argmin", "max", "") and not has_daily and not has_hourly:
            if "daily total" not in msg and "not the hour" not in msg:
                return _PEAK_DATE_CLARIFY

    if intent in ("forecast", "probability") or op in ("probability", "prob", "exceedance") or "probability" in msg:
        vague = any(re.search(p, msg) for p in _VAGUE_THRESHOLD_PATTERNS)
        per_loc = any(re.search(p, msg) for p in _PER_LOCATION_PATTERNS)
        table = _latest_location_threshold_table(refs)
        has_numeric = _has_numeric_threshold(params)

        if table and per_loc:
            return None
        if vague and table and not per_loc:
            return _WHICH_THRESHOLD_CLARIFY
        if vague and not table and not has_numeric:
            return _NEED_THRESHOLD_SOURCE

    return None


def slot_ref_key(slot_key: str) -> str:
    return f"session_slot_{slot_key}"


def slot_to_ref(slot_key: str, value: str) -> Dict[str, Any]:
    return {
        "key": slot_ref_key(slot_key),
        "kind": "session_slot",
        "slot_key": slot_key,
        "value": value,
    }


def slots_from_refs(refs: List[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        if ref.get("kind") == "session_slot":
            key = str(ref.get("slot_key") or "").strip()
            val = str(ref.get("value") or "").strip()
            if key and val:
                out[key] = val
    return out


def _latest_location_threshold_table(
    refs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for ref in refs:
        if isinstance(ref, dict) and ref.get("kind") == "location_threshold_table":
            rows = ref.get("rows")
            if isinstance(rows, list) and rows:
                return ref
    return None


def _has_numeric_threshold(params: dict) -> bool:
    threshold = params.get("threshold")
    if isinstance(threshold, (int, float)):
        return True
    if isinstance(threshold, dict):
        return bool(threshold)
    if isinstance(threshold, str):
        try:
            float(threshold.replace(",", ""))
            return True
        except ValueError:
            return False
    thresholds = params.get("thresholds")
    return isinstance(thresholds, dict) and bool(thresholds)


def _norm(val: Any) -> str:
    return str(val or "").strip().lower().replace(" ", "_")


_MAX_IN_DAY_CLARIFY = (
    "Before I proceed — when you say max load in a day, which do you need?\n\n"
    "1. **Peak hourly load** — the single highest MW reading across the 24 hours of that day, or\n"
    "2. **Daily total energy** — the sum of all 24 hourly MW values for that day (MWh).\n\n"
    "These give different answers, so I want to use the calculation you actually need."
)

_PEAK_DATE_CLARIFY = (
    "Before I look this up — for the day when load peaked, which date do you want?\n\n"
    "1. **Peak hour's calendar date** — the local date of the single hour with the highest MW, or\n"
    "2. **Day with the highest daily total** — the calendar day whose sum of 24 hourly MW values is largest.\n\n"
    "Tell me which, and I'll use that method consistently."
)

_WHICH_THRESHOLD_CLARIFY = (
    "I have per-location thresholds from the last result in this conversation. "
    "Should I compare tomorrow's load against **each location's own threshold** "
    "(one probability per zone), or against a **single shared threshold**? "
    "If shared, please give the value."
)

_NEED_THRESHOLD_SOURCE = (
    "I need a clear threshold for the probability calculation — a numeric MW/MWh value, "
    "or tell me to use thresholds from an earlier result in this conversation "
    "(e.g. each location's 2023 daily total)."
)
