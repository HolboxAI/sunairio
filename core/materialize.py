"""Materialize mixed Forecast+Lake SQL: filtered per-table scans, then DuckDB.

Single-backend SQL never uses this path. Mixed UNION ALL still splits on the
backends (aggregations stay in Postgres/Dremio). This module handles the rest:
JOINs, corr, regr_slope, nested SELECT over both backends, etc.

No statistic is hardcoded. DuckDB runs the original compute SQL after scans.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from config.settings import settings
from data.query_result import build_result
from security.sql_guard import (
    _FORECAST_TABLE_MARKERS,
    _matching_paren,
    normalize_sql,
)

logger = logging.getLogger(__name__)

_ALIAS_RESERVED = frozenset({
    "ON", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "OUTER",
    "GROUP", "ORDER", "LIMIT", "HAVING", "UNION", "SELECT", "AS", "AND", "OR",
    "SET", "USING", "NATURAL", "LATERAL",
})

_FILTER_COLUMNS = (
    "initialization",
    "project_name",
    "location",
    "variable",
    "valid_datetime",
    "ensemble_path",
    "region",
    "iso",
    "hour_beginning",
    "ensemble_type",
)

_TIME_COLUMNS = frozenset({"initialization", "valid_datetime", "hour_beginning"})

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+"
    r"(?P<table>glue\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*|"
    + "|".join(re.escape(t) for t in sorted(_FORECAST_TABLE_MARKERS, key=len, reverse=True))
    + r")"
    r"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?",
    re.IGNORECASE,
)

_PRED_START_RE = re.compile(
    r"\b(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\.(?P<col>"
    + "|".join(_FILTER_COLUMNS)
    + r")\s*(?P<op>=|<>|!=|>=|<=|>|<|IN\b|BETWEEN\b)",
    re.IGNORECASE,
)


@dataclass
class TableRef:
    table: str
    alias: Optional[str]
    backend: str

    @property
    def local_name(self) -> str:
        if self.alias:
            return self.alias
        return re.sub(r"[^\w]", "_", self.table)

    @property
    def qualified_from(self) -> str:
        if self.alias:
            return f"{self.table} {self.alias}"
        return self.table


@dataclass
class TableScan:
    ref: TableRef
    sql: str
    predicates: List[str] = field(default_factory=list)


def extract_table_refs(sql: str) -> List[TableRef]:
    text = normalize_sql(sql)
    refs: List[TableRef] = []
    seen = set()
    for match in _TABLE_REF_RE.finditer(text):
        table = match.group("table")
        alias = match.group("alias")
        if alias and alias.upper() in _ALIAS_RESERVED:
            alias = None
        key = (table.lower(), (alias or "").lower())
        if key in seen:
            continue
        seen.add(key)
        backend = "lake" if table.lower().startswith("glue.") else "forecast"
        refs.append(TableRef(table=table, alias=alias, backend=backend))
    return refs


def _other_aliases(sql: str, alias: str, start: int, end: int) -> bool:
    chunk = sql[start:end]
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\.", chunk):
        if match.group(1).lower() != alias.lower():
            return True
    return False


def _read_predicate_end(sql: str, start: int, op: str) -> int:
    depth = 0
    i = start
    in_str = False
    n = len(sql)
    between_and_left = 1 if op.strip().upper() == "BETWEEN" else 0
    while i < n:
        ch = sql[i]
        if in_str:
            if ch == "'" and i + 1 < n and sql[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                break
            depth -= 1
            i += 1
            continue
        if depth == 0:
            rest = sql[i:]
            and_match = re.match(r"\s+AND\b", rest, re.IGNORECASE)
            if and_match and between_and_left:
                between_and_left -= 1
                i += and_match.end()
                continue
            if re.match(
                r"\s+(AND|OR|GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|UNION|JOIN|"
                r"LEFT|RIGHT|INNER|FULL|CROSS|WHERE|ON|SET)\b",
                rest,
                re.IGNORECASE,
            ):
                break
        i += 1
    return i


def extract_alias_predicates(sql: str, alias: str) -> List[str]:
    text = normalize_sql(sql)
    preds: List[str] = []
    for match in _PRED_START_RE.finditer(text):
        if match.group("alias").lower() != alias.lower():
            continue
        end = _read_predicate_end(text, match.end(), match.group("op"))
        pred = text[match.start() : end].strip()
        if not pred:
            continue
        if _other_aliases(text, alias, match.start(), end):
            continue
        preds.append(pred)
    return preds


def _has_time_filter(predicates: Sequence[str]) -> bool:
    blob = " ".join(predicates).lower()
    return any(col in blob for col in _TIME_COLUMNS)


def build_scans(sql: str, *, row_cap: Optional[int] = None) -> List[TableScan]:
    cap = row_cap if row_cap is not None else settings.safety.max_query_rows
    refs = extract_table_refs(sql)
    if not refs:
        raise ValueError("Mixed Forecast+Lake SQL has no recognized ensemble tables")
    backends = {r.backend for r in refs}
    if "forecast" not in backends or "lake" not in backends:
        raise ValueError("Materialize path requires both a Forecast DB table and a Lake table")

    scans: List[TableScan] = []
    for ref in refs:
        alias = ref.alias
        if not alias:
            raise ValueError(
                f"Table {ref.table} needs an alias so filters can be pushed down "
                "(e.g. FROM energy_forecast_ensemble f)"
            )
        preds = extract_alias_predicates(sql, alias)
        if not _has_time_filter(preds):
            raise ValueError(
                f"Refusing to scan {ref.table} {alias}: add an initialization or "
                "valid_datetime filter on that alias. The executor will not download "
                "an unfiltered ensemble table."
            )
        where = " AND ".join(preds)
        scan_sql = (
            f"SELECT * FROM {ref.qualified_from} WHERE {where} LIMIT {cap}"
        )
        scans.append(TableScan(ref=ref, sql=scan_sql, predicates=preds))
    return scans


def rewrite_table_refs_for_duckdb(sql: str, refs: Sequence[TableRef]) -> str:
    text = normalize_sql(sql)
    ordered = sorted(refs, key=lambda r: len(r.table), reverse=True)
    for ref in ordered:
        local = ref.local_name
        if ref.alias:
            pattern = (
                rf"\b(FROM|JOIN)\s+{re.escape(ref.table)}\s+(?:AS\s+)?"
                rf"{re.escape(ref.alias)}\b"
            )
            text = re.sub(pattern, rf"\1 {local}", text, flags=re.IGNORECASE)
        else:
            pattern = rf"\b(FROM|JOIN)\s+{re.escape(ref.table)}\b"
            text = re.sub(pattern, rf"\1 {local}", text, flags=re.IGNORECASE)
    return text


def _rewrite_timestampadd(sql: str) -> str:
    pattern = re.compile(r"TIMESTAMPADD\s*\(", re.IGNORECASE)
    text = sql
    while True:
        match = pattern.search(text)
        if not match:
            return text
        open_idx = match.end() - 1
        close = _matching_paren(text, open_idx)
        if close is None:
            return text
        inner = text[open_idx + 1 : close]
        parts: List[str] = []
        depth = 0
        start = 0
        in_str = False
        for i, ch in enumerate(inner):
            if in_str:
                if ch == "'" and (i + 1 >= len(inner) or inner[i + 1] != "'"):
                    in_str = False
                elif ch == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                    continue
                continue
            if ch == "'":
                in_str = True
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append(inner[start:i].strip())
                start = i + 1
        parts.append(inner[start:].strip())
        if len(parts) != 3:
            return text
        unit, amount, expr = parts
        duck = f"({expr} + INTERVAL {amount} {unit.strip().upper()})"
        text = text[: match.start()] + duck + text[close + 1 :]


def rewrite_compute_sql_for_duckdb(sql: str) -> str:
    text = sql
    text = re.sub(
        r"CONVERT_TIMEZONE\s*\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*(.*?)\s*\)",
        r"(TRY_CAST(\3 AS TIMESTAMPTZ) AT TIME ZONE '\2')",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"\b((?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*)\s+AT\s+TIME\s+ZONE\s+'([^']+)'",
        r"(TRY_CAST(\1 AS TIMESTAMPTZ) AT TIME ZONE '\2')",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"EXTRACT\s*\(\s*(YEAR|MONTH|DAY|HOUR|MINUTE|SECOND|WEEK|DOY|DOW)\s+FROM\s+"
        r"((?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*)\s*\)",
        r"EXTRACT(\1 FROM TRY_CAST(\2 AS TIMESTAMP))",
        text,
        flags=re.IGNORECASE,
    )
    text = _rewrite_timestampadd(text)
    text = re.sub(
        r"percentile_disc\s*\(\s*([0-9.]+)\s*\)\s*WITHIN\s+GROUP\s*\(\s*ORDER\s+BY\s+([^)]+)\)",
        r"quantile_disc(\2, \1)",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"::timestamptz\b", "::TIMESTAMPTZ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bAS\s+(MONTH|HOUR|YEAR|DAY|DATE|WEEK|MINUTE|SECOND)\b",
        r'AS "\1"',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bAS\s+DOUBLE\b", "AS DOUBLE", text, flags=re.IGNORECASE)
    return text


def _duck_type(value) -> str:
    if value is None:
        return "VARCHAR"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int) and not isinstance(value, bool):
        return "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    return "VARCHAR"


def _insert_result(conn, name: str, result: dict) -> None:
    columns = result["columns"]
    rows = result.get("rows") or []
    col_defs = ", ".join(
        f'"{col}" {_duck_type(rows[0][i] if rows else None)}'
        for i, col in enumerate(columns)
    )
    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
    conn.execute(f'CREATE TABLE "{name}" ({col_defs})')
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        insert_cols = ", ".join(f'"{c}"' for c in columns)
        conn.executemany(
            f'INSERT INTO "{name}" ({insert_cols}) VALUES ({placeholders})',
            [tuple(row) for row in rows],
        )


def execute_materialized(
    sql: str,
    request_id: Optional[str] = None,
    acl=None,
    *,
    run_scan=None,
) -> tuple[dict, dict]:
    """Scan each mixed-backend table, then run the statement in DuckDB."""
    import duckdb

    from core.cte_split import duckdb_merge_avgs, try_partitioned_cte_union
    from core.executor import _run_branch
    from security.acl import validate_sql_acl
    from security.sql_guard import ensure_outer_limit, validate_sql

    text = normalize_sql(sql)
    validate_sql(text)
    validate_sql_acl(text, acl)

    cap = settings.safety.max_query_rows
    scan_fn = run_scan or _run_branch
    t0 = time.monotonic()

    partitioned = try_partitioned_cte_union(text)
    if partitioned:
        logger.info("Materialize partitioned CTE union (Forecast subgraph + Lake subgraph)")
        f_res = scan_fn(partitioned.forecast_sql, "forecast", request_id)
        l_res = scan_fn(partitioned.lake_sql, "lake", request_id)
        for label, res in (("forecast", f_res), ("lake", l_res)):
            if res.get("truncated") or (res.get("row_count") or 0) > cap:
                raise ValueError(
                    f"{label} branch hit the {cap}-row cap after grouping. "
                    "Keep month/hour (or similar) aggregation in the SQL."
                )
        conn = duckdb.connect()
        try:
            _insert_result(conn, "_forecast_part", f_res)
            _insert_result(conn, "_lake_part", l_res)
            out_name = partitioned.agg_cte_name or "_merged"
            conn.execute(
                duckdb_merge_avgs(f_res["columns"], partitioned.avg_aliases, out_name)
            )
            compute_sql = rewrite_compute_sql_for_duckdb(partitioned.remainder_sql)
            compute_sql = ensure_outer_limit(compute_sql)
            rel = conn.execute(compute_sql)
            out_columns = [d[0] for d in rel.description] if rel.description else []
            out_rows = [list(row) for row in rel.fetchall()]
        finally:
            conn.close()
        elapsed = (time.monotonic() - t0) * 1000
        truncated = len(out_rows) >= cap
        if truncated:
            out_rows = out_rows[:cap]
        final = build_result(
            out_columns,
            out_rows,
            backend="federated(cte-split+duckdb)",
            query_time_ms=elapsed,
            truncated=truncated,
        )
        detail = {
            "plan": "materialize",
            "mode": "cte_split",
            "scan_count": 2,
            "compute_sql": compute_sql,
            "steps": [
                {
                    "backend": "forecast",
                    "row_count": f_res.get("row_count"),
                    "query_time_ms": f_res.get("query_time_ms"),
                },
                {
                    "backend": "lake",
                    "row_count": l_res.get("row_count"),
                    "query_time_ms": l_res.get("query_time_ms"),
                },
            ],
        }
        return final, detail

    scans = build_scans(text, row_cap=cap)
    step_results = []
    for scan in scans:
        logger.info(
            "Materialize scan backend=%s table=%s alias=%s",
            scan.ref.backend,
            scan.ref.table,
            scan.ref.alias,
        )
        result = scan_fn(scan.sql, scan.ref.backend, request_id)
        if result.get("truncated") or (result.get("row_count") or 0) > cap:
            raise ValueError(
                f"Scan of {scan.ref.table} {scan.ref.alias} hit the {cap}-row cap. "
                "Aggregate each backend to the join grain (hourly P50, daily mean, …) "
                "before joining, or tighten the time filter."
            )
        if not result.get("columns"):
            raise ValueError(f"Scan of {scan.ref.table} returned no columns")
        step_results.append(result)

    refs = [s.ref for s in scans]
    compute_sql = rewrite_compute_sql_for_duckdb(rewrite_table_refs_for_duckdb(text, refs))
    compute_sql = ensure_outer_limit(compute_sql)

    conn = duckdb.connect()
    try:
        for scan, result in zip(scans, step_results):
            _insert_result(conn, scan.ref.local_name, result)
        rel = conn.execute(compute_sql)
        out_columns = [d[0] for d in rel.description] if rel.description else []
        out_rows = [list(row) for row in rel.fetchall()]
    finally:
        conn.close()

    elapsed = (time.monotonic() - t0) * 1000
    truncated = len(out_rows) >= cap
    if truncated:
        out_rows = out_rows[:cap]

    final = build_result(
        out_columns,
        out_rows,
        backend="federated(materialize+duckdb)",
        query_time_ms=elapsed,
        truncated=truncated,
    )
    detail = {
        "plan": "materialize",
        "scan_count": len(scans),
        "compute_sql": compute_sql,
        "steps": [
            {
                "backend": scan.ref.backend,
                "table": scan.ref.table,
                "alias": scan.ref.alias,
                "row_count": res.get("row_count"),
                "query_time_ms": res.get("query_time_ms"),
            }
            for scan, res in zip(scans, step_results)
        ],
    }
    return final, detail
