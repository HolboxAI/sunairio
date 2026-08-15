"""SQL execution router — split UNION ALL branches and route to backends."""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from core.models import AgentEnvelope
from data import forecast_db, lake_db, metadata_db
from security.acl import UserACL, validate_sql_acl
from core.federated_sql import execute_sqlite_on_merged
from security.sql_guard import (
    classify_sql_target,
    extract_first_cte,
    extract_historical_threshold_cte,
    is_cross_db_threshold_sql,
    is_federated_cte_union,
    is_unsupported_mixed_sql,
    normalize_sql,
    rewrite_cross_db_forecast_sql,
    split_union_all,
    validate_sql,
)

logger = logging.getLogger(__name__)

_EXEC_BACKENDS = {
    "forecast": forecast_db.execute_query,
    "lake": lake_db.execute_query,
    "metadata": metadata_db.execute_query,
}

ExecutionPlan = str  # "standard" | "union_all" | "cross_db_threshold" | "federated_cte_union" | "unsupported"


def should_execute(envelope: AgentEnvelope) -> bool:
    if envelope.clarity_required:
        return False
    if envelope.answer_type not in ("Sql", "Metadata"):
        return False
    return bool((envelope.answer or "").strip())


def plan_execution(sql: str) -> ExecutionPlan:
    text = normalize_sql(sql)
    if not text:
        return "standard"
    if is_cross_db_threshold_sql(text):
        return "cross_db_threshold"
    if is_federated_cte_union(text):
        return "federated_cte_union"
    if is_unsupported_mixed_sql(text):
        return "unsupported"
    if len(split_union_all(text)) > 1:
        return "union_all"
    return "standard"


def route_sql(sql: str) -> str:
    return classify_sql_target(sql)


def _run_branch(sql: str, backend: str, request_id: Optional[str], params: Any = None) -> dict:
    fn = _EXEC_BACKENDS.get(backend)
    if fn is None:
        raise ValueError(f"Unknown backend: {backend}")
    return fn(sql, params, request_id)


def _merge_results(results: List[dict]) -> dict:
    if not results:
        raise ValueError("No query results to merge")
    if len(results) == 1:
        return results[0]

    columns = results[0]["columns"]
    rows: list = []
    backends: list[str] = []
    query_ms = 0.0
    truncated = False

    for res in results:
        if res["columns"] != columns:
            raise ValueError(
                f"UNION ALL branch column mismatch: {res['columns']} vs {columns}"
            )
        rows.extend(res["rows"])
        backends.append(res.get("backend", "unknown"))
        query_ms += float(res.get("query_time_ms", 0) or 0)
        truncated = truncated or bool(res.get("truncated"))

    backend_label = backends[0] if len(set(backends)) == 1 else "merge(" + "+".join(sorted(set(backends))) + ")"
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "query_time_ms": round(query_ms, 1),
        "backend": backend_label,
    }


def _threshold_column_name(cte_body: str, metadata_columns: List[str]) -> str:
    aliases = re.findall(r"\bAS\s+(\w+)", cte_body, re.IGNORECASE)
    if aliases:
        return aliases[-1]
    for col in metadata_columns:
        if "peak" in col.lower():
            return col
    if metadata_columns:
        return metadata_columns[0]
    raise ValueError("Could not determine threshold column from historical query")


def _extract_threshold_value(metadata_result: dict, threshold_column: str) -> float:
    rows = metadata_result.get("rows") or []
    if not rows:
        raise ValueError("Historical threshold query returned no rows")
    columns = metadata_result.get("columns") or []
    try:
        col_idx = columns.index(threshold_column)
    except ValueError:
        col_idx = 0
    value = rows[0][col_idx]
    if value is None:
        raise ValueError("Historical threshold is NULL")
    return float(value)


def execute_cross_db_threshold(
    sql: str,
    request_id: Optional[str] = None,
    acl: Optional[UserACL] = None,
) -> tuple[dict, dict]:
    """Run historical CTE on metadata, then forecast query with bound threshold."""
    parsed = extract_historical_threshold_cte(sql) or extract_first_cte(sql)
    if not parsed:
        raise ValueError("Invalid cross-database threshold SQL")

    cte_name, cte_body, remainder = parsed
    validate_sql(cte_body)

    cross_join = re.search(
        rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+(\w+)\b",
        remainder,
        re.IGNORECASE,
    )
    if not cross_join:
        raise ValueError("Cross-database threshold SQL missing CROSS JOIN to historical CTE")
    cte_alias = cross_join.group(1)

    logger.info("Cross-DB step 1: metadata threshold query")
    metadata_result = _run_branch(cte_body, "metadata", request_id)
    threshold_column = _threshold_column_name(cte_body, metadata_result["columns"])
    peak_mw = _extract_threshold_value(metadata_result, threshold_column)

    forecast_sql, bind_count = rewrite_cross_db_forecast_sql(
        remainder,
        cte_name,
        cte_alias,
        threshold_column,
    )
    validate_sql(forecast_sql)
    validate_sql_acl(forecast_sql, acl)

    logger.info("Cross-DB step 2: forecast query with threshold=%s", peak_mw)
    forecast_params = tuple([peak_mw] * bind_count)
    forecast_result = _run_branch(forecast_sql, "forecast", request_id, forecast_params)

    merged = {
        **forecast_result,
        "backend": f"cross_db(metadata+forecast)",
        "query_time_ms": round(
            float(metadata_result.get("query_time_ms", 0) or 0)
            + float(forecast_result.get("query_time_ms", 0) or 0),
            1,
        ),
    }
    execution_detail = {
        "plan": "cross_db_threshold",
        "threshold_mw": peak_mw,
        "steps": [
            {
                "backend": metadata_result.get("backend", "metadata"),
                "row_count": metadata_result.get("row_count"),
                "query_time_ms": metadata_result.get("query_time_ms"),
            },
            {
                "backend": forecast_result.get("backend", "forecast"),
                "row_count": forecast_result.get("row_count"),
                "query_time_ms": forecast_result.get("query_time_ms"),
            },
        ],
    }
    return merged, execution_detail


def execute_federated_cte_union(
    sql: str,
    request_id: Optional[str] = None,
    acl: Optional[UserACL] = None,
) -> tuple[dict, dict]:
    """Execute UNION ALL inside a CTE across forecast/lake backends, then outer SELECT."""
    parsed = extract_first_cte(sql)
    if not parsed:
        raise ValueError("Invalid federated CTE SQL")

    cte_name, cte_body, remainder = parsed
    branches = split_union_all(cte_body)
    if len(branches) < 2:
        raise ValueError("Federated CTE requires at least two UNION ALL branches")

    step_results: List[dict] = []
    backends: List[str] = []
    for branch in branches:
        validate_sql(branch)
        validate_sql_acl(branch, acl)
        backend = classify_sql_target(branch)
        logger.info("Federated CTE branch on backend=%s", backend)
        step_results.append(_run_branch(branch, backend, request_id))
        backends.append(backend)

    merged = _merge_results(step_results)
    backend_label = (
        backends[0]
        if len(set(backends)) == 1
        else "federated(" + "+".join(sorted(set(backends))) + ")"
    )
    final = execute_sqlite_on_merged(merged, cte_name, remainder, backend_label=backend_label)

    execution_detail = {
        "plan": "federated_cte_union",
        "cte_name": cte_name,
        "branch_count": len(branches),
        "steps": [
            {
                "backend": res.get("backend", "unknown"),
                "row_count": res.get("row_count"),
                "query_time_ms": res.get("query_time_ms"),
            }
            for res in step_results
        ],
    }
    return final, execution_detail


def execute(sql: str, request_id: Optional[str] = None, acl: Optional[UserACL] = None) -> dict:
    sql = normalize_sql(sql)
    if not sql:
        raise ValueError("Empty SQL")

    validate_sql(sql)
    validate_sql_acl(sql, acl)

    plan = plan_execution(sql)
    if plan == "unsupported":
        raise ValueError(
            "Unsupported cross-database SQL: historical and forecast tables must use "
            "a WITH ... CROSS JOIN pattern so the orchestrator can run them in two steps"
        )
    if plan == "cross_db_threshold":
        result, _detail = execute_cross_db_threshold(sql, request_id, acl)
        return result
    if plan == "federated_cte_union":
        result, _detail = execute_federated_cte_union(sql, request_id, acl)
        return result

    branches = split_union_all(sql)
    results: List[dict] = []
    for branch in branches:
        backend = classify_sql_target(branch)
        logger.info("Executing branch on backend=%s", backend)
        results.append(_run_branch(branch, backend, request_id))

    return _merge_results(results)


def execute_with_detail(
    sql: str,
    request_id: Optional[str] = None,
    acl: Optional[UserACL] = None,
) -> tuple[dict, Optional[dict]]:
    """Execute SQL and return (result, optional execution_detail for audit)."""
    sql = normalize_sql(sql)
    if not sql:
        raise ValueError("Empty SQL")

    validate_sql(sql)
    validate_sql_acl(sql, acl)

    plan = plan_execution(sql)
    if plan == "unsupported":
        raise ValueError(
            "Unsupported cross-database SQL: historical and forecast tables must use "
            "a WITH ... CROSS JOIN pattern so the orchestrator can run them in two steps"
        )
    if plan == "cross_db_threshold":
        return execute_cross_db_threshold(sql, request_id, acl)
    if plan == "federated_cte_union":
        return execute_federated_cte_union(sql, request_id, acl)

    result = execute(sql, request_id, acl)
    detail = {"plan": plan}
    if plan == "union_all":
        detail["branch_count"] = len(split_union_all(sql))
    return result, detail
