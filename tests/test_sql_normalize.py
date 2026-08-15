"""SQL normalization before analytics execution."""

from __future__ import annotations

from analytics.llm2.sql_normalize import normalize_analytics_sql


def test_timestamptz_interval_cast_inserted():
    sql = (
        "SELECT 1 WHERE valid_datetime < '2026-08-12T08:00:00Z' + INTERVAL '18 hours'"
    )
    fixed = normalize_analytics_sql(sql)
    assert "'2026-08-12T08:00:00Z'::timestamptz + INTERVAL '18 hours'" in fixed


def test_already_cast_timestamptz_unchanged():
    sql = (
        "SELECT 1 WHERE valid_datetime < '2026-08-12T08:00:00Z'::timestamptz + INTERVAL '18 hours'"
    )
    assert normalize_analytics_sql(sql) == sql
