"""Data Lake — Arrow Flight SQL client and read-only execution."""

from __future__ import annotations

import base64
import logging
import time
from decimal import Decimal
from typing import Optional

import pyarrow.flight as flight

from config.settings import settings
from data.query_result import build_result
from security.sql_guard import ensure_outer_limit, validate_sql

logger = logging.getLogger(__name__)

_client: Optional[flight.FlightClient] = None
_auth_header: Optional[bytes] = None


def init_client() -> None:
    global _client, _auth_header
    if not settings.lake.host:
        logger.warning("LAKE_HOST not set; lake client skipped")
        return
    uri = f"grpc+tls://{settings.lake.host}:{settings.lake.port}"
    _client = flight.FlightClient(uri)
    token = base64.b64encode(f"{settings.lake.user}:{settings.lake.password}".encode()).decode()
    _auth_header = f"Basic {token}".encode()
    logger.info("Lake Flight SQL client initialized (%s)", uri)


def close_client() -> None:
    global _client, _auth_header
    _client = None
    _auth_header = None


def _call_options() -> flight.FlightCallOptions:
    return flight.FlightCallOptions(headers=[(b"authorization", _auth_header)])


def ping() -> bool:
    if _client is None:
        return False
    info = _client.get_flight_info(
        flight.FlightDescriptor.for_command("SELECT 1 AS ok"),
        _call_options(),
    )
    reader = _client.do_get(info.endpoints[0].ticket, _call_options())
    table = reader.read_all()
    return table.num_rows >= 1


def execute_query(sql: str, params: Optional[dict] = None, request_id: Optional[str] = None) -> dict:
    if _client is None:
        raise RuntimeError("Lake client not initialized")

    sql = ensure_outer_limit(sql)
    validate_sql(sql)
    cap = settings.safety.max_query_rows

    t0 = time.monotonic()
    info = _client.get_flight_info(flight.FlightDescriptor.for_command(sql), _call_options())
    reader = _client.do_get(info.endpoints[0].ticket, _call_options())
    table = reader.read_all()
    elapsed = time.monotonic() - t0

    columns = list(table.column_names)
    rows = []
    for i in range(table.num_rows):
        row = []
        for col in columns:
            val = table.column(col)[i].as_py()
            if val is None:
                row.append(None)
            elif hasattr(val, "isoformat"):
                row.append(val.isoformat())
            elif isinstance(val, Decimal):
                row.append(float(val))
            else:
                row.append(val)
        rows.append(row)

    truncated = len(rows) > cap
    if truncated:
        rows = rows[:cap]
    return build_result(columns, rows, backend="lake", query_time_ms=elapsed * 1000, truncated=truncated)
