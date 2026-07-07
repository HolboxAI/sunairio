"""Forecast DB — connection pool and ping (execute stub for v1)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.pool

from config.settings import settings
from data.pg_pool import acquire_connection, is_stale_connection_error, pool_connect_kwargs, release_connection

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
    raise NotImplementedError("SQL execution is phase 2; v1 returns LLM envelope only")
