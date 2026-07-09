"""Tests for SQL guard helpers."""

import pytest

from security.sql_guard import (
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
