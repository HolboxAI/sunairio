"""Safe {{step_id.column}} placeholder parsing and binding."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}\}")


class UnresolvedPlaceholderError(ValueError):
    pass


def find_placeholders(sql: str) -> List[Tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in PLACEHOLDER_RE.finditer(sql or "")]


def has_placeholders(sql: str) -> bool:
    return bool(PLACEHOLDER_RE.search(sql or ""))


def _sql_literal(value: Any, dialect: str) -> str:
    if value is None:
        raise UnresolvedPlaceholderError("Cannot bind NULL placeholder value")
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise UnresolvedPlaceholderError(f"Non-finite numeric placeholder: {value}")
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        text = value.isoformat(sep=" ")
        return "'" + text.replace("'", "''") + "'"
    if isinstance(value, date):
        return "'" + value.isoformat() + "'"
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def bind_sql(
    sql: str,
    values: Dict[str, Dict[str, Any]],
    *,
    dialect: str = "postgres",
    parameterized: bool = False,
) -> Tuple[str, Tuple[Any, ...]]:
    """Replace {{step.col}} with escaped literals (default) or %s parameters.

    Literals are escaped per dialect so the bound SQL is safe to execute and to
    display. ``values`` maps step_id -> {column: scalar_or_list}.
    """
    if not sql:
        return sql, ()
    params: List[Any] = []
    pieces: List[str] = []
    last = 0
    use_params = parameterized and dialect != "lake"
    for match in PLACEHOLDER_RE.finditer(sql):
        step_id, column = match.group(1), match.group(2)
        if step_id not in values or column not in values[step_id]:
            raise UnresolvedPlaceholderError(
                f"Unresolved placeholder {{{{{step_id}.{column}}}}}"
            )
        raw = values[step_id][column]
        pieces.append(sql[last : match.start()])
        if isinstance(raw, (list, tuple)):
            if not raw:
                raise UnresolvedPlaceholderError(
                    f"Placeholder {{{{{step_id}.{column}}}}} has no values"
                )
            if use_params:
                chunks = []
                for item in raw:
                    chunks.append("%s")
                    params.append(item)
                pieces.append(", ".join(chunks))
            else:
                pieces.append(", ".join(_sql_literal(v, dialect) for v in raw))
        else:
            if use_params:
                pieces.append("%s")
                params.append(raw)
            else:
                pieces.append(_sql_literal(raw, dialect))
        last = match.end()
    pieces.append(sql[last:])
    bound = "".join(pieces)
    if PLACEHOLDER_RE.search(bound):
        raise UnresolvedPlaceholderError("Unresolved placeholders remain after binding")
    return bound, tuple(params)


def coerce_value(value: Any, declared_type: str) -> Any:
    if value is None:
        raise ValueError("NULL value where a required result was expected")
    kind = (declared_type or "string").lower()
    if kind == "number":
        if isinstance(value, bool):
            raise ValueError("Boolean is not a number")
        if isinstance(value, (int, float, Decimal)):
            return float(value) if isinstance(value, Decimal) else value
        try:
            if isinstance(value, str) and "." not in value and "e" not in value.lower():
                return int(value)
            return float(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"Cannot coerce {value!r} to number") from e
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in ("true", "t", "yes", "1"):
            return True
        if text in ("false", "f", "no", "0"):
            return False
        raise ValueError(f"Cannot coerce {value!r} to boolean")
    if kind == "timestamp":
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    return str(value)


def extract_contract_values(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    returns: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate result shape against returns contracts and extract bindable values."""
    from planner.models import ColumnContract

    col_index = {str(c): i for i, c in enumerate(columns)}
    col_index_l = {str(c).lower(): i for i, c in enumerate(columns)}
    declared = {
        name: (val if isinstance(val, ColumnContract) else ColumnContract.from_dict(val))
        for name, val in (returns or {}).items()
    }
    if not declared:
        raise ValueError("Step returns contract is empty")

    unexpected = [c for c in columns if str(c) not in declared and str(c).lower() not in {k.lower() for k in declared}]
    if unexpected:
        raise ValueError(f"Unexpected columns: {unexpected}")

    missing = []
    resolved: Dict[str, Any] = {}
    for name, contract in declared.items():
        idx = col_index.get(name)
        if idx is None:
            idx = col_index_l.get(name.lower())
        if idx is None:
            missing.append(name)
            continue
        cardinality = contract.cardinality
        if cardinality == "one":
            if len(rows) != 1:
                raise ValueError(
                    f"Column {name} requires cardinality one, got {len(rows)} row(s)"
                )
            resolved[name] = coerce_value(rows[0][idx], contract.type)
        else:
            if not rows:
                raise ValueError(f"Column {name} requires values, got 0 rows")
            resolved[name] = [coerce_value(row[idx], contract.type) for row in rows]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    return resolved
