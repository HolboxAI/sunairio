"""In-memory final aggregation for federated multi-backend SQL."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import List

from config.settings import settings
from data.query_result import build_result


def rewrite_extract_for_sqlite(sql: str) -> str:
    """Best-effort PostgreSQL EXTRACT → SQLite strftime for outer queries."""
    sql = re.sub(
        r"EXTRACT\s*\(\s*YEAR\s+FROM\s+([^)]+)\)",
        r"CAST(strftime('%Y', \1) AS INTEGER)",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"EXTRACT\s*\(\s*MONTH\s+FROM\s+([^)]+)\)",
        r"CAST(strftime('%m', \1) AS INTEGER)",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _sqlite_type_name(value) -> str:
    if value is None:
        return "TEXT"
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def execute_sqlite_on_merged(
    merged: dict,
    table_name: str,
    remainder_sql: str,
    *,
    backend_label: str,
) -> dict:
    """Run outer SELECT against merged branch rows registered as a SQLite table."""
    columns: List[str] = merged["columns"]
    rows: List[list] = merged["rows"]
    if not columns:
        raise ValueError("Federated merge produced no columns")

    safe_table = re.sub(r"[^\w]", "_", table_name) or "federated_cte"
    col_defs = ", ".join(f'"{col}" {_sqlite_type_name(rows[0][i] if rows else None)}' for i, col in enumerate(columns))
    insert_cols = ", ".join(f'"{col}"' for col in columns)
    placeholders = ", ".join("?" for _ in columns)

    outer_sql = rewrite_extract_for_sqlite(remainder_sql.strip())
    cap = settings.safety.max_query_rows

    t0 = time.monotonic()
    conn = sqlite3.connect(":memory:")
    try:
        cur = conn.cursor()
        cur.execute(f'CREATE TABLE "{safe_table}" ({col_defs})')
        if rows:
            cur.executemany(
                f'INSERT INTO "{safe_table}" ({insert_cols}) VALUES ({placeholders})',
                rows,
            )

        # Rewrite CTE name references to the registered SQLite table name.
        outer_sql = re.sub(
            rf"\b{re.escape(table_name)}\b",
            safe_table,
            outer_sql,
            flags=re.IGNORECASE,
        )

        if not re.search(r"\bLIMIT\s+\d+\b", outer_sql, re.IGNORECASE):
            outer_sql = f"{outer_sql} LIMIT {cap}"

        cur.execute(outer_sql)
        out_columns = [d[0] for d in cur.description or []]
        out_rows = [list(row) for row in cur.fetchall()]
    finally:
        conn.close()

    elapsed = (time.monotonic() - t0) * 1000
    truncated = len(out_rows) >= cap
    if truncated:
        out_rows = out_rows[:cap]

    query_ms = float(merged.get("query_time_ms", 0) or 0) + elapsed
    return build_result(
        out_columns,
        out_rows,
        backend=backend_label,
        query_time_ms=query_ms,
        truncated=truncated or bool(merged.get("truncated")),
    )
