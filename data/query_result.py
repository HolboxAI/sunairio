"""Shared helpers for SQL query result serialization."""

from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any, List


def serialize_row(row) -> List[Any]:
    out: List[Any] = []
    for val in row:
        if val is None:
            out.append(None)
        elif hasattr(val, "isoformat"):
            out.append(val.isoformat())
        elif isinstance(val, Decimal):
            out.append(float(val))
        else:
            out.append(val)
    return out


def estimate_volume(rows: List[list]) -> int:
    volume = sys.getsizeof(rows)
    for row in rows:
        volume += sys.getsizeof(row)
        for val in row:
            volume += sys.getsizeof(val) if val is not None else 0
    return volume


def build_result(
    columns: List[str],
    rows: List[list],
    *,
    backend: str,
    query_time_ms: float,
    truncated: bool,
) -> dict:
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "query_time_ms": round(query_time_ms, 1),
        "backend": backend,
    }
