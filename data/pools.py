"""Lifecycle management for all data backends."""

from __future__ import annotations

import logging
from typing import Dict

from data import forecast_db, lake_db, metadata_db

logger = logging.getLogger(__name__)


def init_all() -> None:
    metadata_db.init_pool()
    try:
        metadata_db.load_variable_units_cache()
    except Exception as e:
        logger.warning("variable_units cache load failed: %s", e)
    forecast_db.init_pool()
    lake_db.init_client()


def close_all() -> None:
    metadata_db.close_pool()
    forecast_db.close_pool()
    lake_db.close_client()


def ping_all() -> Dict[str, bool]:
    results: Dict[str, bool] = {"app_db": True}
    for name, backend in (("metadata", metadata_db), ("forecast", forecast_db), ("lake", lake_db)):
        try:
            results[name] = backend.ping()
        except Exception as e:
            logger.error("%s ping failed: %s", name, e)
            results[name] = False
    return results
