"""Tests for mixed-backend materialize (scans + DuckDB)."""

import re

from unittest.mock import patch

import pytest

from core.executor import execute_with_detail, plan_execution
YEAR_CTE_SQL = """
WITH wind_forecast AS (
  SELECT valid_datetime, ensemble_path, ensemble_value AS wind_val
  FROM energy_forecast_ensemble
  WHERE initialization = '2026-08-18 04:00:00+00'::timestamptz
    AND project_name = 'pjm_generic' AND location = 'pjm' AND variable = 'wind_gen'
    AND valid_datetime <= '2026-08-18 04:00:00+00'::timestamptz + interval '336 hours'
),
solar_forecast AS (
  SELECT valid_datetime, ensemble_path, ensemble_value AS solar_val
  FROM energy_forecast_ensemble
  WHERE initialization = '2026-08-18 04:00:00+00'::timestamptz
    AND project_name = 'pjm_generic' AND location = 'pjm' AND variable = 'solar_gen'
    AND valid_datetime <= '2026-08-18 04:00:00+00'::timestamptz + interval '336 hours'
),
forecast_combined AS (
  SELECT wf.valid_datetime, wf.ensemble_path, wf.wind_val + sf.solar_val AS renewable_val
  FROM wind_forecast wf
  JOIN solar_forecast sf ON wf.valid_datetime = sf.valid_datetime AND wf.ensemble_path = sf.ensemble_path
),
wind_lake AS (
  SELECT valid_datetime, ensemble_path, CAST(ensemble_value AS DOUBLE) AS wind_val
  FROM glue.sunairio.energy_base_ensemble
  WHERE initialization = '2026-08-14 00:00:00+00' AND project_name = 'pjm_generic'
    AND location = 'pjm' AND variable = 'wind_gen'
    AND valid_datetime > CAST('2026-09-01 04:00:00+00' AS TIMESTAMP)
),
solar_lake AS (
  SELECT valid_datetime, ensemble_path, CAST(ensemble_value AS DOUBLE) AS solar_val
  FROM glue.sunairio.energy_base_ensemble
  WHERE initialization = '2026-08-14 00:00:00+00' AND project_name = 'pjm_generic'
    AND location = 'pjm' AND variable = 'solar_gen'
    AND valid_datetime > CAST('2026-09-01 04:00:00+00' AS TIMESTAMP)
),
lake_combined AS (
  SELECT wl.valid_datetime, wl.ensemble_path, wl.wind_val + sl.solar_val AS renewable_val
  FROM wind_lake wl
  JOIN solar_lake sl ON wl.valid_datetime = sl.valid_datetime AND wl.ensemble_path = sl.ensemble_path
),
all_combined AS (
  SELECT valid_datetime, ensemble_path, renewable_val FROM forecast_combined
  UNION ALL
  SELECT valid_datetime, ensemble_path, renewable_val FROM lake_combined
),
bucketed AS (
  SELECT
    EXTRACT(MONTH FROM valid_datetime AT TIME ZONE 'US/Eastern') AS month_num,
    EXTRACT(HOUR FROM valid_datetime AT TIME ZONE 'US/Eastern') AS hour_of_day,
    AVG(renewable_val) AS avg_renewable_mwh
  FROM all_combined
  GROUP BY 1, 2
)
SELECT month_num, hour_of_day, avg_renewable_mwh
FROM bucketed
ORDER BY avg_renewable_mwh ASC
LIMIT 1
"""

from core.cte_split import qualify_ambiguous_join_select, try_partitioned_cte_union
from core.materialize import (
    build_scans,
    extract_alias_predicates,
    extract_table_refs,
    rewrite_compute_sql_for_duckdb,
    rewrite_table_refs_for_duckdb,
)
from tests.test_executor import FEDERATED_MONTHLY_SQL, WRAPPED_FEDERATED_SQL

MIXED_JOIN_SQL = (
    "SELECT corr(f.ensemble_value, b.ensemble_value) AS r "
    "FROM energy_forecast_ensemble f "
    "JOIN glue.sunairio.energy_base_ensemble b "
    "ON f.ensemble_path = b.ensemble_path "
    "WHERE f.initialization = '2026-08-18 03:00:00+00' "
    "AND b.initialization = '2026-08-14 00:00:00+00' "
    "AND f.project_name = 'pjm_generic' "
    "AND b.project_name = 'pjm_generic' "
    "AND f.variable = 'load' "
    "AND b.variable = 'load' "
    "AND f.valid_datetime >= '2026-08-18 03:00:00+00' "
    "AND f.valid_datetime < '2026-09-01 03:00:00+00' "
    "AND b.valid_datetime >= '2027-08-18 03:00:00+00' "
    "AND b.valid_datetime < '2027-09-01 03:00:00+00'"
)

TOP_LEVEL_MIXED_UNION = (
    "SELECT DATE(valid_datetime) AS day, AVG(ensemble_value) AS p50 "
    "FROM energy_forecast_ensemble WHERE project_name = 'pjm_generic' GROUP BY 1 "
    "UNION ALL "
    "SELECT CAST(valid_datetime AS DATE) AS day, AVG(ensemble_value) AS p50 "
    "FROM glue.sunairio.energy_base_ensemble WHERE project_name = 'pjm_generic' GROUP BY 1"
)

FORECAST_ONLY = (
    "SELECT valid_datetime, ensemble_value FROM energy_forecast_ensemble "
    "WHERE project_name = 'pjm_generic' AND initialization = '2026-08-18 03:00:00+00'"
)


def test_plan_keeps_single_backend_standard():
    assert plan_execution(FORECAST_ONLY) == "standard"


def test_plan_keeps_mixed_union_split():
    assert plan_execution(TOP_LEVEL_MIXED_UNION) == "union_all"


def test_plan_keeps_wrapped_federated_union():
    assert plan_execution(WRAPPED_FEDERATED_SQL) == "federated_cte_union"
    assert plan_execution(FEDERATED_MONTHLY_SQL) == "federated_cte_union"


def test_plan_mixed_join_is_materialize():
    assert plan_execution(MIXED_JOIN_SQL) == "materialize"


def test_extract_table_refs_and_predicates():
    refs = extract_table_refs(MIXED_JOIN_SQL)
    assert {(r.backend, r.alias) for r in refs} == {("forecast", "f"), ("lake", "b")}
    f_preds = extract_alias_predicates(MIXED_JOIN_SQL, "f")
    b_preds = extract_alias_predicates(MIXED_JOIN_SQL, "b")
    assert any("initialization" in p for p in f_preds)
    assert any("valid_datetime" in p for p in f_preds)
    assert any("initialization" in p for p in b_preds)
    assert not any("ensemble_path" in p and "b.ensemble_path" in p for p in f_preds)


def test_build_scans_require_time_filter():
    sql = (
        "SELECT corr(f.ensemble_value, b.ensemble_value) AS r "
        "FROM energy_forecast_ensemble f "
        "JOIN glue.sunairio.energy_base_ensemble b ON f.ensemble_path = b.ensemble_path "
        "WHERE f.project_name = 'pjm_generic' AND b.project_name = 'pjm_generic'"
    )
    with pytest.raises(ValueError, match="initialization or valid_datetime"):
        build_scans(sql)


def test_rewrite_table_refs_and_timestampadd():
    refs = extract_table_refs(MIXED_JOIN_SQL)
    rewritten = rewrite_table_refs_for_duckdb(MIXED_JOIN_SQL, refs)
    assert "energy_forecast_ensemble" not in rewritten.lower()
    assert "glue." not in rewritten.lower()
    assert re.search(r"\bFROM\s+f\b", rewritten, re.I)
    assert re.search(r"\bJOIN\s+b\b", rewritten, re.I)
    duck = rewrite_compute_sql_for_duckdb(
        "SELECT * FROM x WHERE ts > TIMESTAMPADD(HOUR, 336, CAST('2026-01-01' AS TIMESTAMP))"
    )
    assert "INTERVAL 336 HOUR" in duck
    assert "TIMESTAMPADD" not in duck.upper()


@patch("core.executor._run_branch")
def test_execute_materialized_corr(mock_run):
    mock_run.side_effect = [
        {
            "columns": [
                "ensemble_path",
                "ensemble_value",
                "initialization",
                "project_name",
                "variable",
                "valid_datetime",
            ],
            "rows": [
                [1, 1.0, "2026-08-18 03:00:00+00", "pjm_generic", "load", "2026-08-18 04:00:00+00"],
                [2, 2.0, "2026-08-18 03:00:00+00", "pjm_generic", "load", "2026-08-18 05:00:00+00"],
                [3, 3.0, "2026-08-18 03:00:00+00", "pjm_generic", "load", "2026-08-18 06:00:00+00"],
            ],
            "row_count": 3,
            "truncated": False,
            "query_time_ms": 1.0,
            "backend": "forecast",
        },
        {
            "columns": [
                "ensemble_path",
                "ensemble_value",
                "initialization",
                "project_name",
                "variable",
                "valid_datetime",
            ],
            "rows": [
                [1, 2.0, "2026-08-14 00:00:00+00", "pjm_generic", "load", "2027-08-18 04:00:00+00"],
                [2, 4.0, "2026-08-14 00:00:00+00", "pjm_generic", "load", "2027-08-18 05:00:00+00"],
                [3, 6.0, "2026-08-14 00:00:00+00", "pjm_generic", "load", "2027-08-18 06:00:00+00"],
            ],
            "row_count": 3,
            "truncated": False,
            "query_time_ms": 2.0,
            "backend": "lake",
        },
    ]
    result, detail = execute_with_detail(MIXED_JOIN_SQL, request_id="req-mat")
    assert detail["plan"] == "materialize"
    assert detail["scan_count"] == 2
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][1] == "forecast"
    assert mock_run.call_args_list[1][0][1] == "lake"
    assert "energy_forecast_ensemble" in mock_run.call_args_list[0][0][0]
    assert "glue.sunairio" in mock_run.call_args_list[1][0][0]
    assert result["row_count"] == 1
    assert abs(result["rows"][0][0] - 1.0) < 1e-9
    assert "duckdb" in result["backend"]


def test_year_cte_sql_is_partitioned_not_alias_error():
    part = try_partitioned_cte_union(YEAR_CTE_SQL)
    assert part is not None
    assert "energy_forecast_ensemble" in part.forecast_sql
    assert "glue.sunairio.energy_base_ensemble" in part.lake_sql
    assert "energy_forecast_ensemble" not in part.lake_sql
    assert "glue." not in part.forecast_sql.lower()
    assert "FROM forecast_combined" in part.forecast_sql
    assert "FROM lake_combined" in part.lake_sql
    assert "_sum__avg_renewable_mwh" in part.forecast_sql
    assert part.agg_cte_name == "bucketed"


@patch("core.executor._run_branch")
def test_execute_year_cte_split_picks_lowest_bucket(mock_run):
    cols = ["month_num", "hour_of_day", "_sum__avg_renewable_mwh", "_n__avg_renewable_mwh"]
    mock_run.side_effect = [
        {
            "columns": cols,
            "rows": [[8, 3, 10.0, 2], [1, 0, 40.0, 2]],
            "row_count": 2,
            "truncated": False,
            "query_time_ms": 4.0,
            "backend": "forecast",
        },
        {
            "columns": cols,
            "rows": [[8, 3, 30.0, 2], [12, 23, 4.0, 4]],
            "row_count": 2,
            "truncated": False,
            "query_time_ms": 6.0,
            "backend": "lake",
        },
    ]
    result, detail = execute_with_detail(YEAR_CTE_SQL, request_id="req-year")
    assert detail["mode"] == "cte_split"
    assert mock_run.call_args_list[0][0][1] == "forecast"
    assert mock_run.call_args_list[1][0][1] == "lake"
    assert result["rows"][0][0] == 12
    assert result["rows"][0][1] == 23
    assert abs(result["rows"][0][2] - 1.0) < 1e-9


def test_qualify_ambiguous_month_on_join():
    body = (
        "SELECT MONTH, hour_of_day, ensemble_path, wind_val + solar_val AS renewable_gen "
        "FROM wind_forecast wf "
        "JOIN solar_forecast sf ON wf.month = sf.month "
        "AND wf.hour_of_day = sf.hour_of_day AND wf.ensemble_path = sf.ensemble_path"
    )
    out = qualify_ambiguous_join_select(body)
    assert re.search(r"\bwf\.MONTH\b", out, re.I)
    assert re.search(r"\bwf\.hour_of_day\b", out, re.I)
    assert re.search(r"\bwf\.ensemble_path\b", out, re.I)
    assert "wind_val + solar_val" in out
    assert not re.search(r"SELECT\s+MONTH\s*,", out, re.I)
