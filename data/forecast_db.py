"""Forecast DB — connection pool and read-only query execution."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.pool

from config.settings import settings
from data.pg_pool import acquire_connection, is_stale_connection_error, pool_connect_kwargs, release_connection
from data.query_result import build_result, serialize_row
from security.sql_guard import ensure_outer_limit, validate_sql

logger = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def init_pool(min_conn: int = 1, max_conn: int = 5) -> None:
    global _pool
    if not settings.forecast_db.host:
        logger.warning("FORECAST_DB_HOST not set; forecast pool skipped")
        return
    _pool = psycopg2.pool.ThreadedConnectionPool(
        min_conn, max_conn, **pool_connect_kwargs(settings.forecast_db)
    )
    logger.info("Forecast DB pool initialized")


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None


@contextmanager
def get_connection():
    if _pool is None:
        raise RuntimeError("Forecast DB pool not initialized")
    conn, discard = acquire_connection(_pool, log_name="Forecast DB")
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        if is_stale_connection_error(e):
            discard = True
        raise
    finally:
        release_connection(_pool, conn, close=discard)


def ping() -> bool:
    if _pool is None:
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone()[0] == 1


def execute_query(sql: str, params: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
    sql = ensure_outer_limit(sql)
    validate_sql(sql)
    cap = settings.safety.max_query_rows
    timeout_ms = settings.safety.query_timeout_sec * 1000

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = ON")
            cur.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")
            t0 = time.monotonic()
            cur.execute(sql, params or None)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            raw_rows = cur.fetchmany(cap + 1)
            elapsed = time.monotonic() - t0

    truncated = len(raw_rows) > cap
    if truncated:
        raw_rows = raw_rows[:cap]
    rows = [serialize_row(r) for r in raw_rows]
    return build_result(columns, rows, backend="forecast", query_time_ms=elapsed * 1000, truncated=truncated)
