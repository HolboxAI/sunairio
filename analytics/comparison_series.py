"""Detect and describe multi-statistic comparison series on confirm cards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from analytics.models import ResolverContext
from analytics.multi_variable import is_variable_comparison


@dataclass(frozen=True)
class ComparisonSeries:
    """One statistic in a side-by-side comparison."""

    label: str
    operation: str
    value: Optional[Any] = None
    trim_pct: Optional[int] = None

    def short_label(self) -> str:
        return self.label or _default_label(self.operation, self.value, self.trim_pct)


def extract_comparison_series(ctx: ResolverContext) -> List[ComparisonSeries]:
    """Return two or more series when the plan compares statistics side by side."""
    if is_variable_comparison(ctx):
        return []

    found: List[ComparisonSeries] = []

    stats = ctx.statistics or {}
    params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
    op = str(stats.get("operation") or "").lower()

    for key in ("series", "statistics", "representations", "stats"):
        raw = params.get(key)
        if isinstance(raw, list) and len(raw) >= 2:
            found = [_series_from_dict(item) for item in raw if isinstance(item, dict)]
            if len(found) >= 2:
                return found

    comparison = ctx.comparison or {}
    if comparison.get("enabled"):
        for key in ("series", "statistics", "values"):
            raw = comparison.get(key)
            if isinstance(raw, list) and len(raw) >= 2:
                parsed = [_series_from_dict(item) for item in raw if isinstance(item, dict)]
                if len(parsed) >= 2:
                    return parsed

    viz = ctx.aep.query.visualization if ctx.aep and ctx.aep.query else None
    y_axes = list(viz.y_axis or []) if viz else []
    if len(y_axes) >= 2:
        found = []
        for y in y_axes:
            if isinstance(y, dict):
                meaning = str(y.get("meaning") or y.get("label") or "").strip()
                if meaning:
                    found.append(_series_from_label(meaning))
        if len(found) >= 2:
            return found

    legend = (ctx.visualization or {}).get("legend") or (viz.legend if viz else None)
    if legend and "|" in str(legend):
        parts = [p.strip() for p in str(legend).split("|") if p.strip()]
        if len(parts) >= 2:
            return [_series_from_label(p) for p in parts]

    if op in ("multi", "multiple", "comparison") and params.get("operations"):
        raw_ops = params.get("operations")
        if isinstance(raw_ops, list) and len(raw_ops) >= 2:
            return [_series_from_dict(item) for item in raw_ops if isinstance(item, dict)]

    return []


def format_multi_representation(series: List[ComparisonSeries]) -> str:
    if len(series) >= 2:
        return "Multi"
    if len(series) == 1:
        return series[0].short_label()
    return ""


def series_summary_labels(series: List[ComparisonSeries]) -> str:
    return ", ".join(s.short_label() for s in series)


def _series_from_dict(item: Dict[str, Any]) -> ComparisonSeries:
    label = str(item.get("label") or item.get("name") or "").strip()
    operation = str(item.get("operation") or item.get("type") or "").lower()
    value = item.get("value")
    params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    trim = _int_or_none(params.get("trim_pct") or params.get("trim") or item.get("trim_pct"))
    if not label:
        label = _default_label(operation, value, trim)
    if not operation and label:
        parsed = _series_from_label(label)
        return ComparisonSeries(
            label=label or parsed.label,
            operation=parsed.operation or operation,
            value=parsed.value if parsed.value is not None else value,
            trim_pct=parsed.trim_pct if parsed.trim_pct is not None else trim,
        )
    return ComparisonSeries(label=label, operation=operation, value=value, trim_pct=trim)


def _series_from_label(label: str) -> ComparisonSeries:
    text = (label or "").strip()
    lower = text.lower()
    trim = _parse_trim_from_label(text)

    if "trimmed" in lower or "winsor" in lower:
        return ComparisonSeries(label=text, operation="trimmed_mean", trim_pct=trim or 20)
    if "mean" in lower and "trimmed" not in lower and "median" not in lower:
        return ComparisonSeries(label=text, operation="mean")
    if "median" in lower or "p50" in lower:
        return ComparisonSeries(label=text, operation="percentile", value=50)

    m = re.search(r"\bp(\d{1,2})\b", lower)
    if m:
        return ComparisonSeries(label=text, operation="percentile", value=int(m.group(1)))

    return ComparisonSeries(label=text, operation="")


def _default_label(operation: str, value: Any, trim_pct: Optional[int]) -> str:
    op = (operation or "").lower()
    if op in ("percentile", "p", "median", "p50"):
        p = _int_or_none(value) or 50
        return f"P{p}" + (" (median)" if p == 50 else "")
    if op in ("mean", "average", "avg"):
        return "Mean"
    if op in ("trimmed_mean", "trim_mean", "winsorized_mean"):
        trim = trim_pct if trim_pct is not None else 20
        lo = trim
        hi = 100 - trim
        return f"Trimmed Mean (P{lo}–P{hi})"
    if op:
        return op.replace("_", " ").title()
    return "Statistic"


def _parse_trim_from_label(label: str) -> Optional[int]:
    m = re.search(r"p(\d{1,2})\s*[–\-—]\s*p(\d{1,2})", label, re.IGNORECASE)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2))
    if hi > lo:
        return lo
    return None


def _int_or_none(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None
