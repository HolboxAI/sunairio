"""Tests for federated CTE UNION detection and final aggregation."""

from core.federated_sql import execute_sqlite_on_merged, rewrite_extract_for_sqlite
from security.sql_guard import is_federated_cte_union

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
