"""Read-only SQL validation (stub for v1; used in phase 2 execution)."""

from __future__ import annotations

_FORBIDDEN = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
})


def validate_sql(sql: str) -> None:
    upper = (sql or "").upper()
    for kw in _FORBIDDEN:
        if kw in upper:
            raise ValueError(f"Forbidden SQL keyword: {kw}")


def classify_sql_target(sql: str) -> str:
    upper = (sql or "").upper()
    if "GLUE." in upper or "GLUE.SUNAIRIO" in upper:
        return "lake"
    if "HISTORICAL_ISO" in upper:
        return "historical"
    if any(t in upper for t in ("ENTITIES", "RESOURCES", "LOCATIONS", "VARIABLES", "USER_ENTITIES")):
        return "metadata"
    return "forecast"
