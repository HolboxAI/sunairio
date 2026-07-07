"""Shared PostgreSQL pool helpers."""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import psycopg2

logger = logging.getLogger(__name__)

_STALE_ERROR_TOKENS = (
    "ssl connection has been closed",
    "connection already closed",
    "server closed the connection",
    "could not receive data",
    "connection reset",
    "broken pipe",
)

_DEFAULT_MAX_ATTEMPTS = 5


def pool_connect_kwargs(cfg) -> dict:
    return dict(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.name,
        user=cfg.user,
        password=cfg.password,
        sslmode=cfg.sslmode,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )


def is_stale_connection_error(exc: BaseException) -> bool:
    if not isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        return False
    msg = str(exc).lower()
    return any(token in msg for token in _STALE_ERROR_TOKENS)


def _ping(conn) -> None:
    if conn.closed:
        raise psycopg2.InterfaceError("connection already closed")
    with conn.cursor() as cur:
        cur.execute("SELECT 1")


def acquire_connection(pool, *, log_name: str, max_attempts: int = _DEFAULT_MAX_ATTEMPTS) -> Tuple[object, bool]:
    last_err: Optional[BaseException] = None
    for attempt in range(max_attempts):
        conn = pool.getconn()
        try:
            _ping(conn)
            return conn, False
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            last_err = e
            pool.putconn(conn, close=True)
            if is_stale_connection_error(e) and attempt < max_attempts - 1:
                logger.warning("%s stale connection (attempt %d/%d): %s", log_name, attempt + 1, max_attempts, e)
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError(f"{log_name}: failed to acquire live connection")


def release_connection(pool, conn, *, close: bool = False) -> None:
    pool.putconn(conn, close=close)
