"""Dependency-aware query-plan executor.

Does not interpret analytical meaning. It executes SQL, validates result
contracts, binds placeholders, and routes each step to its target backend.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

from planner.models import PlanStep, QueryPlan
from planner.placeholders import (
    UnresolvedPlaceholderError,
    bind_sql,
    extract_contract_values,
    has_placeholders,
)
from security.acl import UserACL, validate_sql_acl
from security.sql_guard import (
    classify_sql_target,
    has_glue_table,
    has_native_forecast_table,
    is_federated_union_sql,
    is_mixed_forecast_lake_sql,
    normalize_sql,
    split_union_all,
    validate_sql,
)

logger = logging.getLogger(__name__)

ExecuteFn = Callable[..., dict]


class PlanExecutionError(ValueError):
    pass


def topological_layers(plan: QueryPlan) -> List[List[PlanStep]]:
    """Group steps so independent nodes in a layer may run in parallel."""
    by_id = plan.step_map()
    indegree = {s.id: 0 for s in plan.steps}
    children: Dict[str, List[str]] = defaultdict(list)
    for step in plan.steps:
        for dep in step.depends_on:
            if dep not in by_id:
                raise PlanExecutionError(f"Unknown dependency {dep!r} for step {step.id!r}")
            indegree[step.id] += 1
            children[dep].append(step.id)

    queue = deque([sid for sid, n in indegree.items() if n == 0])
    layers: List[List[PlanStep]] = []
    seen = 0
    while queue:
        layer_ids = list(queue)
        queue.clear()
        layers.append([by_id[sid] for sid in layer_ids])
        seen += len(layer_ids)
        for sid in layer_ids:
            for child in children[sid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
    if seen != len(plan.steps):
        raise PlanExecutionError("query_plan has a dependency cycle")
    return layers


def _dialect_for(target: str) -> str:
    return "lake" if target == "lake" else "postgres"


def is_federated_union_all(sql: str) -> bool:
    """True when core.executor must route mixed Forecast+Lake SQL (not a single backend)."""
    text = normalize_sql(sql)
    if is_federated_union_sql(text) or is_mixed_forecast_lake_sql(text):
        return True
    if not has_glue_table(text) or not has_native_forecast_table(text):
        return False
    return len(split_union_all(text)) > 1


def _run_sql(
    sql: str,
    target: str,
    request_id: Optional[str],
    acl: Optional[UserACL],
    params: Tuple[Any, ...] = (),
    execute_fn: Optional[ExecuteFn] = None,
) -> dict:
    from core import executor as core_executor

    text = normalize_sql(sql)
    validate_sql(text)
    validate_sql_acl(text, acl)
    federated = is_federated_union_all(text)
    if not federated:
        classified = classify_sql_target(text)
        if classified != target:
            logger.warning(
                "Step target %s disagrees with SQL classification %s; using classification",
                target,
                classified,
            )
            target = classified
    if execute_fn is not None:
        return execute_fn(text, target, request_id, params)
    if federated:
        return core_executor.execute(text, request_id, acl)
    if params:
        return core_executor._run_branch(text, target, request_id, params)
    return core_executor.execute(text, request_id, acl)


def execute_step(
    step: PlanStep,
    bound_values: Dict[str, Dict[str, Any]],
    request_id: Optional[str],
    acl: Optional[UserACL],
    execute_fn: Optional[ExecuteFn] = None,
) -> Tuple[PlanStep, Dict[str, Any], dict]:
    federated = is_federated_union_all(step.sql)
    dialect = _dialect_for(step.target)
    display_sql, _ = bind_sql(step.sql, bound_values, dialect=dialect, parameterized=False)
    exec_sql, params = bind_sql(
        step.sql,
        bound_values,
        dialect=dialect,
        parameterized=(dialect != "lake" and not federated),
    )
    if has_placeholders(display_sql) or has_placeholders(exec_sql):
        raise UnresolvedPlaceholderError(f"Step {step.id!r} still has placeholders")
    step.bound_sql = display_sql
    result = _run_sql(exec_sql, step.target, request_id, acl, params, execute_fn)
    extracted = extract_contract_values(
        result.get("columns") or [],
        result.get("rows") or [],
        step.returns,
    )
    step.resolved = extracted
    return step, extracted, result


def execute_plan(
    plan: QueryPlan,
    request_id: Optional[str] = None,
    acl: Optional[UserACL] = None,
    *,
    skip_final: bool = False,
    execute_fn: Optional[ExecuteFn] = None,
) -> Tuple[QueryPlan, Optional[dict], Dict[str, Dict[str, Any]]]:
    """Execute the DAG. When skip_final is True, stop before final_step.

    Returns (updated plan, final result or None, bound_values).
    """
    bound_values: Dict[str, Dict[str, Any]] = {}
    final_result: Optional[dict] = None
    layers = topological_layers(plan)

    for layer in layers:
        runnable = []
        for step in layer:
            if skip_final and step.id == plan.final_step:
                dialect = _dialect_for(step.target)
                bound_sql, _params = bind_sql(step.sql, bound_values, dialect=dialect)
                step.bound_sql = bound_sql
                continue
            runnable.append(step)
        if not runnable:
            continue
        if len(runnable) == 1:
            step, extracted, result = execute_step(
                runnable[0], bound_values, request_id, acl, execute_fn
            )
            bound_values[step.id] = extracted
            if step.id == plan.final_step:
                final_result = result
            continue

        with ThreadPoolExecutor(max_workers=min(8, len(runnable))) as pool:
            futures = {
                pool.submit(
                    execute_step, step, bound_values, request_id, acl, execute_fn
                ): step.id
                for step in runnable
            }
            for fut in as_completed(futures):
                step, extracted, result = fut.result()
                bound_values[step.id] = extracted
                if step.id == plan.final_step:
                    final_result = result

    return plan, final_result, bound_values
