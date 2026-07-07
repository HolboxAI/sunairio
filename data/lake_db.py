"""Data Lake — Arrow Flight SQL client and ping (execute stub for v1)."""

from __future__ import annotations

import base64
import logging
from typing import Optional

import pyarrow.flight as flight

from config.settings import settings

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
    raise NotImplementedError("SQL execution is phase 2; v1 returns LLM envelope only")
