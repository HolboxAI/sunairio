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
    "fundamental_price_forecast_ensemble",
    "fundamental_price_balmo_ensemble",
    "fundamental_price_base_ensemble",
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


_SUBQUERY_ALIAS_RESERVED = {
    "WHERE",
    "GROUP",
    "ORDER",
    "LIMIT",
    "HAVING",
    "UNION",
    "SELECT",
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "CROSS",
    "ON",
    "AND",
    "OR",
}


def _with_ctes_and_remainder(sql: str) -> tuple[list[tuple[str, str]], str]:
    """Return all WITH CTEs and the trailing SELECT (or next clause)."""
    text = normalize_sql(sql)
    ctes = _iter_cte_definitions(text)
    if not ctes:
        return [], text
    last_name = ctes[-1][0]
    matches = list(re.finditer(rf"\b{re.escape(last_name)}\s+AS\s*\(", text, re.IGNORECASE))
    if not matches:
        return ctes, text
    close = _matching_paren(text, matches[-1].end() - 1)
    if close is None:
        return ctes, text
    remainder = text[close + 1 :].strip()
    if remainder.startswith(","):
        remainder = remainder[1:].strip()
    return ctes, remainder


def extract_federated_union_parts(sql: str) -> tuple[str, str, str] | None:
    """Return (alias, union_body, remainder_sql) for mixed Forecast+Lake UNION ALL.

    Supports:
    - WITH cte AS (branch UNION ALL branch) SELECT ... FROM cte
    - WITH union_cte AS (...), extra AS (SELECT ... FROM union_cte) SELECT ...
    - SELECT ... FROM (branch UNION ALL branch) alias ...
    """
    if is_cross_db_threshold_sql(sql):
        return None
    ctes, remainder = _with_ctes_and_remainder(sql)
    for name, body in ctes:
        if not re.search(r"\bUNION\s+ALL\b", body, re.IGNORECASE):
            continue
        if not has_glue_table(body) or not has_native_forecast_table(body):
            continue
        if len(split_union_all(body)) < 2:
            continue
        others = [(n, b) for n, b in ctes if n.lower() != name.lower()]
        if others:
            duck_rest = (
                "WITH "
                + ", ".join(f"{n} AS ({b})" for n, b in others)
                + " "
                + remainder
            )
        else:
            duck_rest = remainder
        head = duck_rest.lstrip().upper()
        if not (head.startswith("SELECT") or head.startswith("WITH")):
            continue
        return name, body, duck_rest
    return extract_derived_table_union(sql)


def extract_derived_table_union(sql: str) -> tuple[str, str, str] | None:
    """SELECT ... FROM (forecast_branch UNION ALL lake_branch) alias ..."""
    text = normalize_sql(sql)
    if not re.search(r"\bUNION\s+ALL\b", text, re.IGNORECASE):
        return None
    if not has_glue_table(text) or not has_native_forecast_table(text):
        return None
    if len(split_union_all(text)) > 1:
        return None

    n = len(text)
    i = 0
    depth = 0
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
        if depth == 0 and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            if text[i : i + 4].upper() == "FROM" and (
                i + 4 >= n or not (text[i + 4].isalnum() or text[i + 4] == "_")
            ):
                after = text[i + 4 :]
                stripped = after.lstrip()
                if stripped.startswith("("):
                    from_start = i
                    open_paren = i + 4 + (len(after) - len(stripped))
                    close = _matching_paren(text, open_paren)
                    if close is None:
                        return None
                    body = text[open_paren + 1 : close].strip()
                    if len(split_union_all(body)) < 2:
                        i += 1
                        continue
                    if not has_glue_table(body) or not has_native_forecast_table(body):
                        i += 1
                        continue
                    tail = text[close + 1 :].lstrip()
                    alias = "federated_subquery"
                    alias_match = re.match(
                        r"(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*)\b", tail, re.IGNORECASE
                    )
                    if alias_match and alias_match.group(1).upper() not in _SUBQUERY_ALIAS_RESERVED:
                        alias = alias_match.group(1)
                        tail = tail[alias_match.end() :].lstrip()
                    remainder = text[:from_start].rstrip() + f" FROM {alias}"
                    if tail:
                        remainder = remainder + " " + tail
                    if not remainder.upper().lstrip().startswith("SELECT"):
                        return None
                    return alias, body, remainder
        i += 1
    return None


def _matching_paren(text: str, open_idx: int) -> int | None:
    if open_idx >= len(text) or text[open_idx] != "(":
        return None
    depth = 1
    j = open_idx + 1
    while j < len(text) and depth > 0:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
        j += 1
    if depth != 0:
        return None
    return j - 1


def is_federated_cte_union(sql: str) -> bool:
    """WITH cte AS (union of forecast + lake branches) then outer SELECT from cte."""
    parts = extract_federated_union_parts(sql)
    if not parts:
        return False
    parsed = extract_first_cte(sql)
    return bool(parsed and parts[0] == parsed[0])


def is_federated_derived_union(sql: str) -> bool:
    """Outer SELECT wrapping a mixed Forecast+Lake UNION ALL subquery."""
    return extract_derived_table_union(sql) is not None


def is_federated_union_sql(sql: str) -> bool:
    return extract_federated_union_parts(sql) is not None


def is_mixed_forecast_lake_sql(sql: str) -> bool:
    """Native Forecast DB table and a glue.* Lake table in the same statement."""
    return has_glue_table(sql) and has_native_forecast_table(sql)


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
    if parsed:
        cte_name, cte_body, remainder = parsed
        if has_historical_iso_table(cte_body) and has_forecast_table(remainder):
            cross_join = re.search(
                rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+(\w+)\b",
                remainder,
                re.IGNORECASE,
            )
            if cross_join is not None:
                return True

    # Multi-CTE: historical peak CTE + forecast CTE(s), CROSS JOIN in outer SELECT.
    for cte_name, cte_body in _iter_cte_definitions(sql):
        if not has_historical_iso_table(cte_body):
            continue
        if re.search(
            rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+\w+\b",
            sql,
            re.IGNORECASE,
        ):
            return True
    return False


def _iter_cte_definitions(sql: str) -> list[tuple[str, str]]:
    """Return (cte_name, cte_body) pairs from a WITH clause."""
    text = normalize_sql(sql)
    if not re.match(r"WITH\s", text, re.IGNORECASE):
        return []

    i = re.match(r"WITH\s+", text, re.IGNORECASE).end()
    ctes: list[tuple[str, str]] = []
    while i < len(text):
        name_match = re.match(r"(\w+)\s+AS\s*\(", text[i:], re.IGNORECASE)
        if not name_match:
            break
        name = name_match.group(1)
        start = i + name_match.end()
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        if depth != 0:
            break
        body = text[start : j - 1].strip()
        ctes.append((name, body))
        i = j
        rest = text[i:].lstrip()
        if rest.startswith(","):
            comma_at = i + (len(text[i:]) - len(rest))
            i = comma_at + 1
            while i < len(text) and text[i].isspace():
                i += 1
            continue
        break
    return ctes


def extract_historical_threshold_cte(sql: str) -> tuple[str, str, str] | None:
    """Return (cte_name, cte_body, remainder_sql) for cross-DB threshold execution."""
    parsed = extract_first_cte(sql)
    if parsed:
        cte_name, cte_body, remainder = parsed
        if (
            has_historical_iso_table(cte_body)
            and has_forecast_table(remainder)
            and re.search(
                rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+\w+\b",
                remainder,
                re.IGNORECASE,
            )
        ):
            return cte_name, cte_body, remainder

    text = normalize_sql(sql)
    for cte_name, cte_body in _iter_cte_definitions(text):
        if not has_historical_iso_table(cte_body):
            continue
        cross = re.search(
            rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+(\w+)\b",
            text,
            re.IGNORECASE,
        )
        if not cross:
            continue
        remainder = _remove_cte_from_with(text, cte_name)
        if has_forecast_table(remainder):
            return cte_name, cte_body, remainder
    return None


def _remove_cte_from_with(sql: str, cte_name: str) -> str:
    text = normalize_sql(sql)
    pattern = rf"(?P<prefix>WITH\s+)?(?P<lead>,?\s*){re.escape(cte_name)}\s+AS\s*\("
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return text

    start = match.start()
    open_paren = match.end() - 1
    depth = 1
    j = open_paren + 1
    while j < len(text) and depth > 0:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
        j += 1
    end = j
    tail = text[end:].lstrip()
    if tail.startswith(","):
        end += 1
        tail = tail[1:].lstrip()
    if match.group("prefix"):
        if tail.upper().startswith("SELECT"):
            return tail
        return f"WITH {tail}"
    return f"{text[:start]}{tail}".strip()


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
