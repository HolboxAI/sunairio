"""Tests for SQL executor routing and merge."""

from unittest.mock import patch

import pytest

from core.executor import execute, execute_with_detail, plan_execution, should_execute
from core.models import AgentEnvelope
from security.acl import UserACL

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


def _envelope(**kwargs) -> AgentEnvelope:
    defaults = {
        "clarity_required": False,
        "clarifying_question": None,
        "question": "q",
        "answer_type": "Sql",
        "assumption": [],
        "answer": "SELECT 1",
    }
    defaults.update(kwargs)
    return AgentEnvelope(**defaults)


def test_should_execute_skips_clarity_and_awareness():
    assert should_execute(_envelope()) is True
    assert should_execute(_envelope(clarity_required=True)) is False
    assert should_execute(_envelope(answer_type="Awareness", answer="text")) is False
    assert should_execute(_envelope(answer="")) is False


def test_should_execute_metadata():
    assert should_execute(_envelope(answer_type="Metadata", answer="SELECT * FROM entities")) is True


def _forecast_result():
    return {
        "columns": ["n"],
        "rows": [[1]],
        "row_count": 1,
        "truncated": False,
        "query_time_ms": 5.0,
        "backend": "forecast",
    }


@patch("core.executor._run_branch")
def test_execute_single_branch(mock_run):
    mock_run.return_value = _forecast_result()
    result = execute("SELECT 1", request_id="req-1")
    assert result["backend"] == "forecast"
    assert result["row_count"] == 1
    mock_run.assert_called_once()


@patch("core.executor._run_branch")
def test_execute_union_all_merge(mock_run):
    mock_run.side_effect = [
        {
            "columns": ["id"],
            "rows": [[1]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 3.0,
            "backend": "forecast",
        },
        {
            "columns": ["id"],
            "rows": [[2]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 2.0,
            "backend": "metadata",
        },
    ]
    sql = "SELECT id FROM energy_forecast_ensemble UNION ALL SELECT id FROM entities"
    result = execute(sql)
    assert result["row_count"] == 2
    assert result["rows"] == [[1], [2]]
    assert "merge" in result["backend"]
    assert mock_run.call_count == 2


def test_execute_acl_denied():
    acl = UserACL(username="u", project_names=["allowed_only"])
    with pytest.raises(ValueError, match="Access denied"):
        execute("SELECT 1 WHERE project_name = \'ercot_generic\'", acl=acl)


@patch("core.executor._run_branch")
def test_execute_union_column_mismatch(mock_run):
    mock_run.side_effect = [
        {
            "columns": ["a"],
            "rows": [[1]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 1.0,
            "backend": "forecast",
        },
        {
            "columns": ["b"],
            "rows": [[2]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 1.0,
            "backend": "metadata",
        },
    ]
    with pytest.raises(ValueError, match="column mismatch"):
        execute(
            "SELECT a FROM energy_forecast_ensemble "
            "UNION ALL SELECT b FROM entities"
        )


def test_plan_execution_cross_db_threshold():
    assert plan_execution(SUMMER_PEAK_SQL) == "cross_db_threshold"


def test_plan_execution_standard_forecast():
    sql = "SELECT 1 FROM energy_forecast_ensemble WHERE project_name = 'ercot_generic'"
    assert plan_execution(sql) == "standard"


@patch("core.executor._run_branch")
def test_execute_cross_db_threshold_two_step(mock_run):
    mock_run.side_effect = [
        {
            "columns": ["peak_mw"],
            "rows": [[45231.0]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 12.0,
            "backend": "metadata",
        },
        {
            "columns": ["probability"],
            "rows": [[0.04]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 80.0,
            "backend": "forecast",
        },
    ]
    result, detail = execute_with_detail(SUMMER_PEAK_SQL, request_id="req-x")
    assert result["row_count"] == 1
    assert "cross_db" in result["backend"]
    assert detail["plan"] == "cross_db_threshold"
    assert detail["threshold_mw"] == 45231.0
    assert len(detail["steps"]) == 2
    assert mock_run.call_count == 2
    metadata_sql = mock_run.call_args_list[0][0][0]
    forecast_sql = mock_run.call_args_list[1][0][0]
    assert "historical_iso_load_gen" in metadata_sql
    assert "energy_forecast_ensemble" in forecast_sql
    assert "historical_iso_load_gen" not in forecast_sql
    assert mock_run.call_args_list[1][0][3] == (45231.0,)


def test_execute_unsupported_mixed_sql_raises():
    sql = (
        "SELECT h.hour_value FROM historical_iso_load_gen h "
        "JOIN energy_forecast_ensemble e ON h.region = e.location"
    )
    with pytest.raises(ValueError, match="Unsupported cross-database SQL"):
        execute(sql)


FEDERATED_MONTHLY_SQL = (
    "WITH hourly_p50 AS ("
    "SELECT valid_datetime, ensemble_value AS p50_temp FROM weather_seasonal_ensemble "
    "WHERE project_name = 'pjm_generic' "
    "UNION ALL "
    "SELECT valid_datetime, ensemble_value AS p50_temp FROM glue.sunairio.weather_base_ensemble "
    "WHERE project_name = 'pjm_generic'"
    ") "
    "SELECT EXTRACT(YEAR FROM valid_datetime) AS \"year\", "
    "EXTRACT(MONTH FROM valid_datetime) AS \"month\", "
    "AVG(p50_temp) AS avg_p50_temp_2m FROM hourly_p50 "
    "GROUP BY EXTRACT(YEAR FROM valid_datetime), EXTRACT(MONTH FROM valid_datetime)"
)


def test_plan_execution_federated_cte_union():
    assert plan_execution(FEDERATED_MONTHLY_SQL) == "federated_cte_union"


@patch("core.executor._run_branch")
def test_execute_federated_cte_union(mock_run):
    mock_run.side_effect = [
        {
            "columns": ["valid_datetime", "p50_temp"],
            "rows": [["2026-08-01T00:00:00+00:00", 20.0], ["2026-08-01T01:00:00+00:00", 24.0]],
            "row_count": 2,
            "truncated": False,
            "query_time_ms": 3.0,
            "backend": "forecast",
        },
        {
            "columns": ["valid_datetime", "p50_temp"],
            "rows": [["2026-10-01T00:00:00+00:00", 15.0]],
            "row_count": 1,
            "truncated": False,
            "query_time_ms": 5.0,
            "backend": "lake",
        },
    ]
    result, detail = execute_with_detail(FEDERATED_MONTHLY_SQL, request_id="req-fed")
    assert detail["plan"] == "federated_cte_union"
    assert detail["branch_count"] == 2
    assert result["columns"] == ["year", "month", "avg_p50_temp_2m"]
    assert len(result["rows"]) == 2
    assert "federated" in result["backend"]
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][1] == "forecast"
    assert mock_run.call_args_list[1][0][1] == "lake"
