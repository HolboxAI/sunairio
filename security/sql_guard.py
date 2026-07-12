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


def has_glue_table(sql: str) -> bool:
    return "GLUE." in _strip_comments(sql).upper()


def has_native_forecast_table(sql: str) -> bool:
    """Forecast DB table reference (FROM/JOIN without glue. catalog prefix)."""
    text = _strip_comments(sql).lower()
    for marker in _FORECAST_TABLE_MARKERS:
        if re.search(rf"\bfrom\s+{re.escape(marker)}\b", text):
            return True
        if re.search(rf"\bjoin\s+{re.escape(marker)}\b", text):
            return True
    return False


def is_federated_cte_union(sql: str) -> bool:
    """WITH cte AS (union of forecast + lake branches) then outer SELECT from cte."""
    if is_cross_db_threshold_sql(sql):
        return False
    parsed = extract_first_cte(sql)
    if not parsed:
        return False
    cte_name, cte_body, remainder = parsed
    if not re.search(r"\bUNION\s+ALL\b", cte_body, re.IGNORECASE):
        return False
    if not has_glue_table(cte_body) or not has_native_forecast_table(cte_body):
        return False
    if not re.search(rf"\b{re.escape(cte_name)}\b", remainder, re.IGNORECASE):
        return False
    return True


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


_TS_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:")

_INTERVAL_UNIT = {
    "day": "DAY",
    "days": "DAY",
    "hour": "HOUR",
    "hours": "HOUR",
    "month": "MONTH",
    "months": "MONTH",
    "week": "WEEK",
    "weeks": "WEEK",
}

_LAKE_CAST_TYPES = {
    "float": "DOUBLE",
    "double": "DOUBLE",
    "int": "INT",
    "integer": "INT",
    "bigint": "BIGINT",
    "numeric": "DECIMAL",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "timestamp": "TIMESTAMP",
}


def _normalize_timestamp_string(content: str) -> str:
    if _TS_ISO_PREFIX.match(content):
        return content.replace("T", " ", 1)
    return content


def _lake_cast_type(pg_type: str) -> str:
    return _LAKE_CAST_TYPES.get(pg_type.lower(), pg_type.upper())


def _rewrite_string_literal_casts(sql: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        literal = _normalize_timestamp_string(match.group(1))
        pg_type = match.group(2).lower()
        if pg_type == "timestamptz":
            return f"'{literal}'"
        return f"CAST('{literal}' AS {_lake_cast_type(pg_type)})"

    return re.sub(
        r"'((?:[^']|'')*)'\s*::\s*(\w+)\b",
        _replace,
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_parenthesized_casts(sql: str) -> str:
    pattern = re.compile(r"\)\s*::\s*(\w+)\b", re.IGNORECASE)
    while True:
        match = pattern.search(sql)
        if not match:
            return sql
        close_paren = match.start()
        depth = 1
        start = close_paren - 1
        while start >= 0 and depth > 0:
            if sql[start] == ")":
                depth += 1
            elif sql[start] == "(":
                depth -= 1
            start -= 1
        start += 1
        fn_start = start
        while fn_start > 0 and (sql[fn_start - 1].isalnum() or sql[fn_start - 1] in "._"):
            fn_start -= 1
        expr = sql[fn_start : close_paren + 1]
        pg_type = match.group(1).lower()
        if pg_type == "timestamptz":
            replacement = expr
        else:
            replacement = f"CAST({expr} AS {_lake_cast_type(pg_type)})"
        sql = sql[:fn_start] + replacement + sql[match.end() :]


def _rewrite_timestamp_with_time_zone_casts(sql: str) -> str:
    return re.sub(
        r"CAST\s*\(\s*'((?:[^']|'')*)'\s+AS\s+TIMESTAMP\s+WITH\s+TIME\s+ZONE\s*\)",
        lambda m: f"CAST('{_normalize_timestamp_string(m.group(1))}' AS TIMESTAMP)",
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_interval_additions(sql: str) -> str:
    def _replace_string_interval(match: re.Match[str]) -> str:
        literal = _normalize_timestamp_string(match.group(1))
        amount = match.group(2)
        unit = _INTERVAL_UNIT[match.group(3).lower()]
        return f"TIMESTAMPADD({unit}, {amount}, '{literal}')"

    def _replace_identifier_interval(match: re.Match[str]) -> str:
        amount = match.group(2)
        unit = _INTERVAL_UNIT[match.group(3).lower()]
        return f"TIMESTAMPADD({unit}, {amount}, {match.group(1)})"

    sql = re.sub(
        r"'((?:[^']|'')*)'\s*\+\s*interval\s+'(\d+)\s+(days?|hours?|months?|weeks?)'",
        _replace_string_interval,
        sql,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"\b([a-zA-Z_][\w.]*)\s*\+\s*interval\s+'(\d+)\s+(days?|hours?|months?|weeks?)'",
        _replace_identifier_interval,
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_at_time_zone(sql: str) -> str:
    return re.sub(
        r"\b((?:[a-zA-Z_][\w.]*|NOW\s*\(\s*\)))\s+AT\s+TIME\s+ZONE\s+'([^']+)'",
        r"CONVERT_TIMEZONE('UTC', '\2', \1)",
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_identifier_casts(sql: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        expr = match.group(1)
        pg_type = match.group(2).lower()
        if pg_type == "timestamptz":
            return expr
        return f"CAST({expr} AS {_lake_cast_type(pg_type)})"

    return re.sub(
        r"\b([a-zA-Z_][\w.]*)\s*::\s*(\w+)\b",
        _replace,
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_generic_pg_casts(sql: str) -> str:
    sql = _rewrite_string_literal_casts(sql)
    sql = _rewrite_parenthesized_casts(sql)
    return _rewrite_identifier_casts(sql)


def _rewrite_regr_slope(sql: str) -> str:
    return re.sub(
        r"\bregr_slope\s*\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
        r"covar_pop(\1, \2) / var_pop(\2)",
        sql,
        flags=re.IGNORECASE,
    )


def _rewrite_remaining_iso_timestamps_in_literals(sql: str) -> str:
    def _rewrite_literal(match: re.Match[str]) -> str:
        content = _normalize_timestamp_string(match.group(1))
        return f"'{content}'"

    return re.sub(r"'((?:[^']|'')*)'", _rewrite_literal, sql)


def _quote_reserved_lake_aliases(sql: str) -> str:
    for word in ("year", "month"):
        sql = re.sub(rf"\bAS\s+{word}\b", f'AS "{word}"', sql, flags=re.IGNORECASE)
    return sql


def adapt_sql_for_lake(sql: str) -> str:
    """Rewrite PostgreSQL-specific syntax for Dremio / Arrow Flight SQL."""
    if not sql:
        return sql
    text = sql
    text = _rewrite_regr_slope(text)
    text = _rewrite_timestamp_with_time_zone_casts(text)
    text = _rewrite_generic_pg_casts(text)
    text = _rewrite_interval_additions(text)
    text = _rewrite_at_time_zone(text)
    text = _rewrite_remaining_iso_timestamps_in_literals(text)
    text = _quote_reserved_lake_aliases(text)
    return text


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
