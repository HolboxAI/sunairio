"""Tests for cross-database threshold SQL detection."""

from security.sql_guard import (
    extract_first_cte,
    is_cross_db_threshold_sql,
    is_unsupported_mixed_sql,
    rewrite_cross_db_forecast_sql,
)

SUMMER_PEAK_SQL = (
    "WITH summer_peak AS ("
    "SELECT MAX(hour_value) AS peak_mw FROM historical_iso_load_gen "
    "WHERE iso = 'ERCOT' AND region = 'north_raybn' AND variable = 'load' "
    "AND EXTRACT(MONTH FROM hour_beginning) IN (6, 7, 8)"
    ") "
    "SELECT COUNT(*)::float / 1000.0 AS probability "
    "FROM energy_forecast_ensemble e "
    "CROSS JOIN summer_peak sp "
    "WHERE e.project_name = 'ercot_generic' "
    "AND e.location = 'north_raybn' "
    "AND e.variable = 'load' "
    "AND e.ensemble_value > sp.peak_mw"
)


def test_is_cross_db_threshold_sql_detects_peak_probability_pattern():
    assert is_cross_db_threshold_sql(SUMMER_PEAK_SQL) is True


def test_extract_first_cte_splits_historical_and_forecast_parts():
    cte_name, cte_body, remainder = extract_first_cte(SUMMER_PEAK_SQL)
    assert cte_name == "summer_peak"
    assert "historical_iso_load_gen" in cte_body
    assert "energy_forecast_ensemble" in remainder
    assert "CROSS JOIN summer_peak sp" in remainder


def test_rewrite_cross_db_forecast_sql_binds_threshold():
    _, _, remainder = extract_first_cte(SUMMER_PEAK_SQL)
    forecast_sql, bind_count = rewrite_cross_db_forecast_sql(
        remainder, "summer_peak", "sp", "peak_mw"
    )
    assert "CROSS JOIN" not in forecast_sql
    assert "sp.peak_mw" not in forecast_sql
    assert "%s" in forecast_sql
    assert bind_count == 1


def test_unsupported_mixed_sql_without_cross_join():
    sql = (
        "SELECT h.hour_value, e.ensemble_value "
        "FROM historical_iso_load_gen h "
        "JOIN energy_forecast_ensemble e ON h.region = e.location"
    )
    assert is_cross_db_threshold_sql(sql) is False
    assert is_unsupported_mixed_sql(sql) is True


def test_forecast_only_sql_not_cross_db():
    sql = "SELECT valid_datetime FROM energy_forecast_ensemble WHERE project_name = 'ercot_generic'"
    assert is_cross_db_threshold_sql(sql) is False
    assert is_unsupported_mixed_sql(sql) is False
