"""Deterministic SQL fixes before analytics Forecast/Metadata execution."""

from __future__ import annotations

import re

# ISO timestamps used in interval math without an explicit cast break PostgreSQL.
_TS_INTERVAL = re.compile(
    r"'((?:[^']|'')*)'(\s*\+\s*(?:INTERVAL|interval)\s+'[^']*')",
    re.IGNORECASE,
)


def normalize_analytics_sql(sql: str) -> str:
    """Apply safe rewrites so LLM2 SQL runs on Forecast/Metadata PostgreSQL."""
    if not sql:
        return sql
    return _fix_timestamptz_interval_arithmetic(sql)


def _fix_timestamptz_interval_arithmetic(sql: str) -> str:
    """Cast string timestamps before + INTERVAL (e.g. '...Z' + INTERVAL '18 hours')."""

    def _repl(match: re.Match[str]) -> str:
        literal = match.group(1)
        rest = match.group(2)
        # Already cast in the substring immediately before +
        start = match.start()
        prefix = sql[max(0, start - 32) : start].lower()
        if prefix.rstrip().endswith("::timestamptz") or prefix.rstrip().endswith("::timestamp"):
            return match.group(0)
        return f"'{literal}'::timestamptz{rest}"

    return _TS_INTERVAL.sub(_repl, sql)
