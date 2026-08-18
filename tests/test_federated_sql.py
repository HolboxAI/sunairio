"""Tests for federated CTE UNION detection and final aggregation."""

from core.federated_sql import execute_duckdb_on_merged, execute_sqlite_on_merged, rewrite_extract_for_sqlite
from security.sql_guard import (
    extract_derived_table_union,
    is_federated_cte_union,
    is_federated_derived_union,
    is_federated_union_sql,
)

FEDERATED_MONTHLY_SQL = (
    "WITH hourly_p50 AS ("
    "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) AS p50_temp "
    "FROM weather_seasonal_ensemble "
    "WHERE initialization = '2026-07-08 00:00:00+00' AND project_name = 'pjm_generic' "
    "AND location = 'pjm' AND variable = 'temp_2m' "
    "AND valid_datetime >= '2026-08-01 00:00:00+00' AND valid_datetime < '2026-10-08 00:00:00+00' "
    "GROUP BY valid_datetime "
    "UNION ALL "
    "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) AS p50_temp "
    "FROM glue.sunairio.weather_seasonal_ensemble "
    "WHERE initialization = '2026-07-08 00:00:00+00' AND project_name = 'pjm_generic' "
    "AND location = 'pjm' AND variable = 'temp_2m' "
    "AND valid_datetime >= '2026-10-08 00:00:00+00' AND valid_datetime < '2028-07-08 00:00:00+00' "
    "GROUP BY valid_datetime"
    ") "
    "SELECT EXTRACT(YEAR FROM valid_datetime) AS \"year\", "
    "EXTRACT(MONTH FROM valid_datetime) AS \"month\", "
    "AVG(p50_temp) AS avg_p50_temp_2m "
    "FROM hourly_p50 "
    "GROUP BY EXTRACT(YEAR FROM valid_datetime), EXTRACT(MONTH FROM valid_datetime) "
    "ORDER BY \"year\", \"month\""
)


def test_is_federated_cte_union_detects_mixed_cte():
    assert is_federated_cte_union(FEDERATED_MONTHLY_SQL) is True


def test_lake_only_cte_not_federated():
    sql = (
        "WITH hourly_p50 AS ("
        "SELECT valid_datetime, ensemble_value AS p50_temp FROM glue.sunairio.weather_base_ensemble"
        ") SELECT AVG(p50_temp) FROM hourly_p50"
    )
    assert is_federated_cte_union(sql) is False


def test_rewrite_extract_for_sqlite():
    sql = "SELECT EXTRACT(YEAR FROM valid_datetime), EXTRACT(MONTH FROM valid_datetime)"
    out = rewrite_extract_for_sqlite(sql)
    assert "strftime('%Y'" in out
    assert "strftime('%m'" in out


def test_execute_sqlite_on_merged_monthly_agg():
    merged = {
        "columns": ["valid_datetime", "p50_temp"],
        "rows": [
            ["2026-08-01T00:00:00+00:00", 20.0],
            ["2026-08-01T01:00:00+00:00", 22.0],
            ["2026-09-01T00:00:00+00:00", 18.0],
        ],
        "row_count": 3,
        "truncated": False,
        "query_time_ms": 1.0,
        "backend": "merge(forecast+lake)",
    }
    remainder = (
        'SELECT EXTRACT(YEAR FROM valid_datetime) AS "year", '
        'EXTRACT(MONTH FROM valid_datetime) AS "month", '
        "AVG(p50_temp) AS avg_p50_temp_2m "
        "FROM hourly_p50 "
        "GROUP BY EXTRACT(YEAR FROM valid_datetime), EXTRACT(MONTH FROM valid_datetime) "
        'ORDER BY "year", "month"'
    )
    result = execute_sqlite_on_merged(merged, "hourly_p50", remainder, backend_label="federated(test)")
    assert result["columns"] == ["year", "month", "avg_p50_temp_2m"]
    assert len(result["rows"]) == 2
    assert result["rows"][0][0] == 2026
    assert result["rows"][0][1] == 8
    assert result["rows"][0][2] == 21.0


def test_execute_duckdb_on_merged_at_time_zone():
    merged = {
        "columns": ["valid_datetime", "renewable_gen"],
        "rows": [
            ["2026-08-18T08:00:00+00:00", 10.0],
            ["2026-08-18T09:00:00+00:00", 20.0],
            ["2026-12-01T05:00:00+00:00", 4.0],
        ],
        "row_count": 3,
        "truncated": False,
        "query_time_ms": 1.0,
        "backend": "merge(forecast+lake)",
    }
    remainder = (
        "SELECT EXTRACT(MONTH FROM valid_datetime AT TIME ZONE 'US/Eastern') AS MONTH, "
        "EXTRACT(HOUR FROM valid_datetime AT TIME ZONE 'US/Eastern') AS hour_of_day, "
        "AVG(renewable_gen) AS avg_renewable_gen "
        "FROM raw GROUP BY 1, 2 ORDER BY 1, 2"
    )
    result = execute_duckdb_on_merged(merged, "raw", remainder, backend_label="federated(test)")
    assert "AT TIME ZONE" not in remainder or result["row_count"] >= 1
    assert result["row_count"] >= 1
    assert result["columns"][0].lower() in ("month", "month_num") or result["columns"][0] == "MONTH"
    hours = {tuple(r[:2]) for r in result["rows"]}
    assert len(hours) >= 2


def test_user_year_union_cte_is_federated():
    sql = (
        "WITH raw AS ("
        "SELECT valid_datetime, ensemble_path, SUM(ensemble_value) AS renewable_gen "
        "FROM energy_forecast_ensemble WHERE project_name = 'pjm_generic' "
        "GROUP BY valid_datetime, ensemble_path "
        "UNION ALL "
        "SELECT valid_datetime, ensemble_path, SUM(ensemble_value) AS renewable_gen "
        "FROM glue.sunairio.energy_base_ensemble WHERE project_name = 'pjm_generic' "
        "GROUP BY valid_datetime, ensemble_path"
        ") SELECT EXTRACT(MONTH FROM valid_datetime AT TIME ZONE 'US/Eastern') AS MONTH, "
        "AVG(renewable_gen) AS avg_renewable_gen FROM raw GROUP BY 1"
    )
    assert is_federated_union_sql(sql) is True


PIVOT_AFTER_UNION_SQL = (
    "WITH raw AS ("
    "SELECT valid_datetime, ensemble_path, ensemble_value, variable "
    "FROM energy_forecast_ensemble "
    "WHERE initialization = '2026-08-18 07:00:00+00'::timestamptz "
    "AND project_name = 'pjm_generic' AND variable IN ('wind_gen', 'solar_gen') "
    "UNION ALL "
    "SELECT valid_datetime, ensemble_path, ensemble_value, variable "
    "FROM glue.sunairio.energy_base_ensemble "
    "WHERE initialization = '2026-08-14 00:00:00+00' "
    "AND project_name = 'pjm_generic' AND variable IN ('wind_gen', 'solar_gen')"
    "), pivoted AS ("
    "SELECT valid_datetime, ensemble_path, "
    "SUM(CASE WHEN variable = 'wind_gen' THEN ensemble_value ELSE 0 END) + "
    "SUM(CASE WHEN variable = 'solar_gen' THEN ensemble_value ELSE 0 END) AS renewable_gen "
    "FROM raw GROUP BY valid_datetime, ensemble_path"
    ") SELECT EXTRACT(MONTH FROM valid_datetime AT TIME ZONE 'US/Eastern') AS month_num, "
    "EXTRACT(HOUR FROM valid_datetime AT TIME ZONE 'US/Eastern') AS hour_of_day, "
    "AVG(renewable_gen) AS avg_renewable_gen FROM pivoted "
    "GROUP BY month_num, hour_of_day ORDER BY month_num, hour_of_day"
)


def test_pivot_cte_after_mixed_union_is_federated():
    from security.sql_guard import extract_federated_union_parts
    from core.executor import plan_execution

    parts = extract_federated_union_parts(PIVOT_AFTER_UNION_SQL)
    assert parts is not None
    name, body, remainder = parts
    assert name == "raw"
    assert "energy_forecast_ensemble" in body
    assert "glue.sunairio" in body
    assert remainder.lstrip().upper().startswith("WITH")
    assert "pivoted" in remainder.lower()
    assert plan_execution(PIVOT_AFTER_UNION_SQL) == "federated_cte_union"


def test_execute_pivot_after_mixed_union():
    from unittest.mock import patch
    from core.executor import execute_with_detail

    cols = ["valid_datetime", "ensemble_path", "ensemble_value", "variable"]
    with patch("core.executor._run_branch") as mock_run:
        mock_run.side_effect = [
            {
                "columns": cols,
                "rows": [
                    ["2026-09-01T08:00:00+00:00", 1, 10.0, "wind_gen"],
                    ["2026-09-01T08:00:00+00:00", 1, 5.0, "solar_gen"],
                ],
                "row_count": 2,
                "truncated": False,
                "query_time_ms": 1.0,
                "backend": "forecast",
            },
            {
                "columns": cols,
                "rows": [
                    ["2026-12-01T10:00:00+00:00", 1, 3.0, "wind_gen"],
                    ["2026-12-01T10:00:00+00:00", 1, 1.0, "solar_gen"],
                ],
                "row_count": 2,
                "truncated": False,
                "query_time_ms": 2.0,
                "backend": "lake",
            },
        ]
        result, detail = execute_with_detail(PIVOT_AFTER_UNION_SQL, request_id="req-pivot")
    assert detail["plan"] == "federated_cte_union"
    assert result["row_count"] == 2
    assert "avg_renewable_gen" in result["columns"]


WRAPPED_FEDERATED_SQL = (
    "SELECT month_of_year, hour_of_day, "
    "SUM(sum_renewable_gen) / CAST(SUM(n) AS DOUBLE) AS avg_renewable_gen "
    "FROM ("
    "SELECT EXTRACT(MONTH FROM wf.valid_datetime AT TIME ZONE 'US/Eastern') AS month_of_year, "
    "EXTRACT(HOUR FROM wf.valid_datetime AT TIME ZONE 'US/Eastern') AS hour_of_day, "
    "SUM(wf.ensemble_value + sf.ensemble_value) AS sum_renewable_gen, COUNT(*) AS n "
    "FROM energy_forecast_ensemble wf "
    "JOIN energy_forecast_ensemble sf ON wf.valid_datetime = sf.valid_datetime "
    "AND wf.ensemble_path = sf.ensemble_path "
    "WHERE wf.variable = 'wind_gen' AND sf.variable = 'solar_gen' GROUP BY 1, 2 "
    "UNION ALL "
    "SELECT EXTRACT(MONTH FROM CONVERT_TIMEZONE('UTC', 'US/Eastern', wb.valid_datetime)) AS month_of_year, "
    "EXTRACT(HOUR FROM CONVERT_TIMEZONE('UTC', 'US/Eastern', wb.valid_datetime)) AS hour_of_day, "
    "SUM(wb.ensemble_value + sb.ensemble_value) AS sum_renewable_gen, COUNT(*) AS n "
    "FROM glue.sunairio.energy_base_ensemble wb "
    "JOIN glue.sunairio.energy_base_ensemble sb ON wb.valid_datetime = sb.valid_datetime "
    "AND wb.ensemble_path = sb.ensemble_path "
    "WHERE wb.variable = 'wind_gen' AND sb.variable = 'solar_gen' GROUP BY 1, 2"
    ") all_combined "
    "GROUP BY month_of_year, hour_of_day "
    "ORDER BY avg_renewable_gen ASC LIMIT 1"
)


def test_is_federated_derived_union_detects_wrapped_union():
    alias, body, remainder = extract_derived_table_union(WRAPPED_FEDERATED_SQL)
    assert alias == "all_combined"
    assert "energy_forecast_ensemble" in body
    assert "glue.sunairio.energy_base_ensemble" in body
    assert "UNION ALL" in body.upper()
    assert "FROM all_combined" in remainder
    assert "energy_forecast_ensemble" not in remainder
    assert is_federated_cte_union(WRAPPED_FEDERATED_SQL) is False
    assert is_federated_derived_union(WRAPPED_FEDERATED_SQL) is True
    assert is_federated_union_sql(WRAPPED_FEDERATED_SQL) is True


def test_rewrite_cast_double_for_sqlite():
    out = rewrite_extract_for_sqlite(
        "SELECT SUM(x) / CAST(SUM(n) AS DOUBLE) AS avg_x FROM all_combined"
    )
    assert "AS REAL" in out
    assert "DOUBLE" not in out.upper()


def test_execute_sqlite_on_merged_weighted_average():
    merged = {
        "columns": ["month_of_year", "hour_of_day", "sum_renewable_gen", "n"],
        "rows": [[6, 3, 10.0, 2], [6, 3, 30.0, 2], [7, 1, 100.0, 1]],
        "row_count": 3,
        "truncated": False,
        "query_time_ms": 1.0,
        "backend": "merge(forecast+lake)",
    }
    remainder = (
        "SELECT month_of_year, hour_of_day, "
        "SUM(sum_renewable_gen) / CAST(SUM(n) AS DOUBLE) AS avg_renewable_gen "
        "FROM all_combined GROUP BY month_of_year, hour_of_day "
        "ORDER BY avg_renewable_gen ASC LIMIT 1"
    )
    result = execute_sqlite_on_merged(
        merged, "all_combined", remainder, backend_label="federated(test)"
    )
    assert result["columns"] == ["month_of_year", "hour_of_day", "avg_renewable_gen"]
    assert result["rows"][0][0] == 6
    assert result["rows"][0][1] == 3
    assert result["rows"][0][2] == 10.0
