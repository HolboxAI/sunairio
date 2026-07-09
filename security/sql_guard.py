"""Read-only SQL validation and routing helpers."""

from __future__ import annotations

import re

from config.settings import settings

_FORBIDDEN_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "EXECUTE", "CALL",
})

_METADATA_TABLES = frozenset({
    "ENTITIES", "RESOURCES", "LOCATIONS", "VARIABLES", "USER_ENTITIES",
    "RESOURCE_TYPES", "LOCATION_VARIABLES", "RESOURCE_VARIABLES", "MARKETS",
    "ENSEMBLE_RUNS",
})

_FORECAST_TABLE_MARKERS = (
    "energy_forecast_ensemble",
    "weather_forecast_ensemble_short",
    "weather_forecast_ensemble_extended",
    "energy_base_ensemble",
    "weather_base_ensemble",
    "energy_seasonal_ensemble",
    "weather_seasonal_ensemble",
    "fundamental_market_ensemble",
)


def _strip_comments(sql: str) -> str:
    cleaned = re.sub(r"--.*?$", " ", sql, flags=re.MULTILINE)
    return re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)


def normalize_sql(sql: str) -> str:
    text = (sql or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|sql)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip().rstrip(";")


def ensure_outer_limit(sql: str) -> str:
    trimmed = normalize_sql(sql)
    if not trimmed:
        return sql
    cap = settings.safety.max_query_rows
    if re.search(r"\bLIMIT\s+\d+\s*;?\s*$", trimmed, re.IGNORECASE):
        match = re.search(r"\bLIMIT\s+(\d+)\s*;?\s*$", trimmed, re.IGNORECASE)
        if match and int(match.group(1)) <= cap:
            return trimmed
        return re.sub(r"\bLIMIT\s+\d+\s*;?\s*$", f" LIMIT {cap}", trimmed, flags=re.IGNORECASE)
    return f"{trimmed} LIMIT {cap}"


def validate_sql(sql: str) -> None:
    cleaned = _strip_comments(sql)
    upper = cleaned.strip().upper()
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        raise ValueError("Only SELECT / WITH queries are allowed")
    no_strings = re.sub(r"'[^']*'", "", upper)
    for token in re.findall(r"[A-Z_]+", no_strings):
        if token in _FORBIDDEN_KEYWORDS:
            raise ValueError(f"Forbidden SQL keyword: {token}")
    statements = [s.strip() for s in cleaned.split(";") if s.strip()]
    if len(statements) > 1:
        raise ValueError("Multiple SQL statements are not allowed")


def extract_project_names(sql: str) -> set:
    return set(re.findall(r"project_name\s*=\s*'([^']+)'", sql, re.IGNORECASE))


def classify_sql_target(sql: str) -> str:
    upper = _strip_comments(sql).upper()
    if "GLUE." in upper:
        return "lake"
    if "HISTORICAL_ISO" in upper:
        return "metadata"
    if any(t in upper for t in _METADATA_TABLES):
        return "metadata"
    return "forecast"


def has_historical_iso_table(sql: str) -> bool:
    return "HISTORICAL_ISO" in _strip_comments(sql).upper()


def has_forecast_table(sql: str) -> bool:
    lower = _strip_comments(sql).lower()
    return any(marker in lower for marker in _FORECAST_TABLE_MARKERS)


def extract_first_cte(sql: str) -> tuple[str, str, str] | None:
    """Return (cte_name, cte_body, remainder) for the first WITH ... AS (...) clause."""
    text = normalize_sql(sql)
    match = re.match(r"WITH\s+(\w+)\s+AS\s*\(", text, re.IGNORECASE)
    if not match:
        return None

    cte_name = match.group(1)
    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None

    cte_body = text[start : i - 1].strip()
    remainder = text[i:].strip()
    if not remainder.upper().startswith("SELECT"):
        return None
    return cte_name, cte_body, remainder


def is_cross_db_threshold_sql(sql: str) -> bool:
    """Historical threshold CTE joined to a forecast ensemble query via CROSS JOIN."""
    if not has_historical_iso_table(sql) or not has_forecast_table(sql):
        return False

    parsed = extract_first_cte(sql)
    if not parsed:
        return False

    cte_name, cte_body, remainder = parsed
    if not has_historical_iso_table(cte_body):
        return False
    if not has_forecast_table(remainder):
        return False

    cross_join = re.search(
        rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+(\w+)\b",
        remainder,
        re.IGNORECASE,
    )
    return cross_join is not None


def is_unsupported_mixed_sql(sql: str) -> bool:
    """Mixed historical + forecast/lake SQL that we cannot execute."""
    upper = _strip_comments(sql).upper()
    if "GLUE." in upper and has_historical_iso_table(sql):
        return not is_cross_db_threshold_sql(sql)
    if has_historical_iso_table(sql) and has_forecast_table(sql):
        return not is_cross_db_threshold_sql(sql)
    return False


def rewrite_cross_db_forecast_sql(
    remainder: str,
    cte_name: str,
    cte_alias: str,
    threshold_column: str,
) -> tuple[str, int]:
    """Drop CROSS JOIN to the historical CTE and bind threshold column references."""
    forecast_sql = re.sub(
        rf"\s+CROSS\s+JOIN\s+{re.escape(cte_name)}\s+{re.escape(cte_alias)}\s*",
        " ",
        remainder,
        count=1,
        flags=re.IGNORECASE,
    )
    pattern = rf"\b{re.escape(cte_alias)}\.{re.escape(threshold_column)}\b"
    forecast_sql, replacements = re.subn(pattern, "%s", forecast_sql, flags=re.IGNORECASE)
    if replacements == 0:
        raise ValueError(
            f"Could not bind threshold column {cte_alias}.{threshold_column} in forecast SQL"
        )
    return forecast_sql, replacements


def split_union_all(sql: str) -> list[str]:
    text = normalize_sql(sql)
    if not re.search(r"\bUNION\s+ALL\b", text, re.IGNORECASE):
        return [text]

    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and text[i : i + 9].upper() == "UNION ALL":
            part = text[start:i].strip()
            if part:
                parts.append(part)
            i += 9
            start = i
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts if parts else [text]
