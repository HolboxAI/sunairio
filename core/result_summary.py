"""Fill human-readable result sentences from SQL output (no fabricated numbers)."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int):
        return str(value)
    return str(value)


def _column_map(columns: Sequence[str], row: Sequence[Any]) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for col, val in zip(columns, row):
        mapping[str(col).strip().lower()] = val
    return mapping


def fill_result_template(
    template: str,
    columns: Sequence[str],
    row: Sequence[Any],
) -> Optional[str]:
    """Substitute `{column_alias}` placeholders with values from a single result row."""
    text = (template or "").strip()
    if not text or not columns or not row:
        return None

    colmap = _column_map(columns, row)
    missing: List[str] = []

    def repl(match: re.Match[str]) -> str:
        cleaned = match.group(1).strip().lower()
        if cleaned in colmap:
            return _format_value(colmap[cleaned])
        if cleaned in {"value", "result"} and len(columns) == 1:
            return _format_value(row[0])
        missing.append(match.group(1).strip())
        return match.group(0)

    filled = _PLACEHOLDER_RE.sub(repl, text)
    if missing:
        return None
    return filled


def _humanize_column(name: str) -> str:
    text = re.sub(r"[_\s]+", " ", (name or "").strip())
    return text.lower() if text else "value"


def build_scalar_fallback(question: str, columns: Sequence[str], row: Sequence[Any]) -> str:
    """Deterministic prose when the model did not supply a result_template."""
    q = (question or "").strip().rstrip("?")
    if len(columns) == 1:
        value = _format_value(row[0])
        label = _humanize_column(columns[0])
        if q:
            return f"The {label} for {q} is {value}."
        return f"The {label} is {value}."

    pairs = "; ".join(
        f"{_humanize_column(col)} = {_format_value(val)}" for col, val in zip(columns, row)
    )
    if q:
        return f"For {q}: {pairs}."
    return f"{pairs}."


def build_result_summary(
    *,
    question: str,
    result_template: Optional[str],
    columns: Optional[Sequence[str]],
    rows: Optional[Sequence[Sequence[Any]]],
) -> Optional[str]:
    """
    Build a user-facing sentence for scalar (single-row) SQL results.

    Multi-row result sets are left to the table/chart; only single-row answers
    get a prose summary.
    """
    if not columns or not rows or len(rows) != 1:
        return None

    row = list(rows[0])
    cols = [str(c) for c in columns]
    if len(row) < len(cols):
        row.extend([None] * (len(cols) - len(row)))
    row = row[: len(cols)]

    if result_template:
        filled = fill_result_template(result_template, cols, row)
        if filled:
            return filled

    return build_scalar_fallback(question, cols, row)


def build_metadata_answer(
    *,
    question: str,
    columns: Optional[Sequence[str]],
    rows: Optional[Sequence[Sequence[Any]]],
) -> str:
    """
    Replace Metadata SQL with a human-term catalog response from executed rows.

    Used as the user-facing `answer` after the orchestrator runs Metadata SQL.
    """
    cols = [str(c) for c in (columns or [])]
    data_rows = [list(r) for r in (rows or [])]
    if not cols:
        return "No catalog data was returned."
    if not data_rows:
        q = (question or "").strip().rstrip("?")
        if q:
            return f"No matching catalog rows were found for: {q}."
        return "No matching catalog rows were found."

    # Single name-like column → compact list sentence.
    if len(cols) == 1:
        values = [_format_value(row[0]) for row in data_rows if row]
        label = _humanize_column(cols[0])
        joined = ", ".join(values)
        if len(values) == 1:
            return f"The {label} is {joined}."
        return f"The {label} values are: {joined}."

    lines: List[str] = []
    for row in data_rows:
        padded = list(row) + [None] * max(0, len(cols) - len(row))
        padded = padded[: len(cols)]
        parts = [
            f"{_humanize_column(col)}: {_format_value(val)}"
            for col, val in zip(cols, padded)
            if val is not None and str(val).strip() != ""
        ]
        if parts:
            lines.append("- " + "; ".join(parts))

    count = len(lines)
    header = f"Found {count} result{'s' if count != 1 else ''}:"
    return header + "\n" + "\n".join(lines) if lines else "No matching catalog rows were found."
