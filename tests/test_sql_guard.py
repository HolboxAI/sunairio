"""Tests for SQL guard helpers."""

import pytest

from security.sql_guard import (
    adapt_sql_for_lake,
    classify_sql_target,
    ensure_outer_limit,
    extract_project_names,
    normalize_sql,
    split_union_all,
    validate_sql,
)


def test_normalize_sql_strips_fences_and_semicolon():
    raw = "```sql\nSELECT 1;\n```"
    assert normalize_sql(raw) == "SELECT 1"


def test_validate_sql_rejects_mutations():
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_sql("DELETE FROM t")
    with pytest.raises(ValueError, match="Forbidden"):
        validate_sql("SELECT 1; INSERT INTO t VALUES (1)")


def test_validate_sql_rejects_multiple_statements():
    with pytest.raises(ValueError, match="Multiple SQL"):
        validate_sql("SELECT 1; SELECT 2")


def test_classify_sql_target():
    assert classify_sql_target("SELECT * FROM energy_forecast_ensemble") == "forecast"
    assert classify_sql_target("SELECT * FROM GLUE.db.table") == "lake"
    assert classify_sql_target("SELECT * FROM entities") == "metadata"
    assert classify_sql_target("SELECT * FROM historical_iso_load_gen") == "metadata"


def test_extract_project_names():
    sql = "SELECT 1 WHERE project_name = \'ercot_generic\' AND other = \'x\'"
    assert extract_project_names(sql) == {"ercot_generic"}


def test_split_union_all_respects_parentheses():
    sql = (
        "(SELECT a FROM t WHERE x IN (SELECT 1 UNION ALL SELECT 2)) "
        "UNION ALL SELECT b FROM t2"
    )
    parts = split_union_all(sql)
    assert len(parts) == 2
    assert parts[0].startswith("(SELECT a")
    assert parts[1].startswith("SELECT b")


def test_ensure_outer_limit_appends_cap():
    limited = ensure_outer_limit("SELECT 1")
    assert limited.endswith("LIMIT 5000")


def test_ensure_outer_limit_replaces_high_limit():
    limited = ensure_outer_limit("SELECT 1 LIMIT 99999")
    assert limited.endswith("LIMIT 5000")


def test_adapt_sql_for_lake_strips_timestamptz_cast():
    sql = (
        "SELECT 1 FROM glue.sunairio.weather_base_ensemble "
        "WHERE initialization = '2026-01-08T00:00:00+00'::timestamptz"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "::timestamptz" not in adapted.lower()
    assert "'2026-01-08 00:00:00+00'" in adapted


def test_adapt_sql_for_lake_rewrites_float_cast():
    sql = "SELECT COUNT(*)::float / 1000.0 FROM glue.t"
    adapted = adapt_sql_for_lake(sql)
    assert adapted == "SELECT CAST(COUNT(*) AS DOUBLE) / 1000.0 FROM glue.t"


def test_adapt_sql_for_lake_rewrites_distinct_float_cast():
    sql = "SELECT COUNT(DISTINCT ensemble_path)::float / 1000.0 FROM glue.t"
    adapted = adapt_sql_for_lake(sql)
    assert "CAST(COUNT(DISTINCT ensemble_path) AS DOUBLE)" in adapted


def test_adapt_sql_for_lake_rewrites_timestamp_with_time_zone_cast():
    sql = (
        "SELECT 1 FROM glue.t WHERE initialization = "
        "CAST('2026-01-08T00:00:00+00' AS TIMESTAMP WITH TIME ZONE)"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "WITH TIME ZONE" not in adapted.upper()
    assert "CAST('2026-01-08 00:00:00+00' AS TIMESTAMP)" in adapted


def test_adapt_sql_for_lake_rewrites_interval_addition():
    sql = (
        "SELECT 1 FROM glue.t WHERE valid_datetime < "
        "'2026-06-21 07:00:00+00' + interval '14 days'"
    )
    adapted = adapt_sql_for_lake(sql)
    assert adapted == (
        "SELECT 1 FROM glue.t WHERE valid_datetime < "
        "TIMESTAMPADD(DAY, 14, '2026-06-21 07:00:00+00')"
    )


def test_adapt_sql_for_lake_rewrites_at_time_zone():
    sql = (
        "SELECT EXTRACT(HOUR FROM valid_datetime AT TIME ZONE 'US/Eastern') "
        "FROM glue.t"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "AT TIME ZONE" not in adapted.upper()
    assert "CONVERT_TIMEZONE('UTC', 'US/Eastern', valid_datetime)" in adapted


def test_adapt_sql_for_lake_rewrites_regr_slope():
    sql = "SELECT regr_slope(e.ensemble_value, w.ensemble_value) FROM glue.t"
    adapted = adapt_sql_for_lake(sql)
    assert adapted == (
        "SELECT covar_pop(e.ensemble_value, w.ensemble_value) / "
        "var_pop(w.ensemble_value) FROM glue.t"
    )


def test_adapt_sql_for_lake_audit_log_query():
    sql = (
        "SELECT AVG(p50_temp) AS avg_p50_temp_2m FROM ("
        "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP "
        "(ORDER BY ensemble_value) AS p50_temp "
        "FROM glue.sunairio.weather_base_ensemble "
        "WHERE initialization = '2026-01-08T00:00:00+00'::timestamptz "
        "AND project_name = 'pjm_generic' AND location = 'pjm' "
        "AND variable = 'temp_2m' "
        "AND valid_datetime >= '2028-08-01 00:00:00+00'::timestamptz "
        "AND valid_datetime < '2028-09-01 00:00:00+00'::timestamptz "
        "GROUP BY valid_datetime) hourly_p50"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "::" not in adapted
    assert "'2026-01-08 00:00:00+00'" in adapted
    assert "percentile_disc(0.5) WITHIN GROUP" in adapted


def test_adapt_sql_for_lake_leaves_non_timestamp_literals():
    sql = "SELECT 1 FROM glue.t WHERE project_name = 'pjm_generic'"
    assert adapt_sql_for_lake(sql) == sql


def test_adapt_sql_for_lake_rewrites_extract_int_cast():
    sql = (
        "SELECT EXTRACT(YEAR FROM valid_datetime)::int AS year, "
        "EXTRACT(MONTH FROM valid_datetime)::int AS month FROM glue.t"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "::" not in adapted
    assert "CAST(EXTRACT(YEAR FROM valid_datetime) AS INT)" in adapted
    assert "CAST(EXTRACT(MONTH FROM valid_datetime) AS INT)" in adapted
    assert 'AS "year"' in adapted
    assert 'AS "month"' in adapted


def test_adapt_sql_for_lake_monthly_audit_query():
    sql = (
        "WITH hourly_p50 AS ("
        "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) AS p50_temp "
        "FROM glue.sunairio.weather_seasonal_ensemble "
        "WHERE initialization = '2026-07-08 00:00:00+00'::timestamptz "
        "AND valid_datetime >= '2026-08-01 00:00:00+00'::timestamptz "
        "AND valid_datetime < '2026-10-08 00:00:00+00'::timestamptz "
        "GROUP BY valid_datetime UNION ALL "
        "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) AS p50_temp "
        "FROM glue.sunairio.weather_base_ensemble "
        "WHERE initialization = '2026-01-08 00:00:00+00'::timestamptz "
        "AND valid_datetime >= '2026-10-08 00:00:00+00'::timestamptz "
        "AND valid_datetime < '2028-08-01 00:00:00+00'::timestamptz "
        "GROUP BY valid_datetime) "
        "SELECT EXTRACT(YEAR FROM valid_datetime)::int AS year, "
        "EXTRACT(MONTH FROM valid_datetime)::int AS month, "
        "AVG(p50_temp) AS avg_p50_temp_2m FROM hourly_p50 GROUP BY 1, 2 ORDER BY 1, 2"
    )
    adapted = adapt_sql_for_lake(sql)
    assert "::" not in adapted
    assert "CAST(EXTRACT(YEAR FROM valid_datetime) AS INT)" in adapted
    assert 'AS "year"' in adapted
    assert 'AS "month"' in adapted
