"""Parse and validate the v3 planner JSON envelope."""

from __future__ import annotations

import re
from typing import Any, List, Optional

from core.response_parser import parse_chart_details
from llm.parsing import parse_json
from planner.models import (
    VALID_CARDINALITY,
    VALID_RETURN_TYPES,
    VALID_TARGETS,
    PlanStep,
    PlannerEnvelope,
    QueryPlan,
)
from planner.placeholders import find_placeholders

VALID_ANSWER_TYPES = {"Sql", "Metadata", "Awareness"}
VALID_CHART_TYPES = {"line", "scatter", "bar"}
# `SELECT a, DISTINCT b` is illegal; `COUNT(DISTINCT x)` / `STRING_AGG(DISTINCT x, ...)` are not.
_MID_LIST_DISTINCT = re.compile(r"(?<!\()\s*,\s*DISTINCT\b", re.IGNORECASE)


def _as_str_list(val: Any) -> Optional[List[str]]:
    if val is None:
        return None
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return None


def parse_envelope(raw_text: str) -> PlannerEnvelope:
    data = parse_json(raw_text)
    assumptions = data.get("assumptions")
    if assumptions is None:
        assumptions = data.get("assumption") or []
    result_template_raw = data.get("result_template")
    result_template = (
        str(result_template_raw).strip()
        if result_template_raw is not None and str(result_template_raw).strip()
        else None
    )
    understanding_raw = data.get("understanding")
    understanding = (
        str(understanding_raw).strip()
        if understanding_raw is not None and str(understanding_raw).strip()
        else None
    )
    rationale_raw = data.get("timeframe_rationale")
    timeframe_rationale = (
        str(rationale_raw).strip()
        if rationale_raw is not None and str(rationale_raw).strip()
        else None
    )
    final_sql_raw = data.get("final_sql")
    final_sql = (
        str(final_sql_raw).strip()
        if final_sql_raw is not None and str(final_sql_raw).strip()
        else None
    )
    answer_raw = data.get("answer")
    answer = (
        str(answer_raw).strip()
        if answer_raw is not None and str(answer_raw).strip()
        else None
    )
    chart_applicable = bool(data.get("chart_applicable", False))
    chart_details = parse_chart_details(
        data.get("chart_details"), legacy_chart_type=data.get("chart_type")
    )
    return PlannerEnvelope(
        clarity_required=bool(data.get("clarity_required", False)),
        clarifying_question=_as_str_list(data.get("clarifying_question")),
        question=str(data.get("question") or ""),
        understanding=understanding,
        timeframe_rationale=timeframe_rationale,
        answer_type=str(data.get("answer_type") or "Sql"),
        assumptions=[str(a) for a in (assumptions or [])],
        suggestions=[str(s) for s in (data.get("suggestions") or []) if str(s).strip()],
        answer=answer,
        query_plan=QueryPlan.from_dict(data.get("query_plan")),
        final_sql=final_sql,
        result_template=result_template,
        chart_applicable=chart_applicable,
        chart_details=chart_details,
    )


def _validate_plan(plan: QueryPlan) -> List[str]:
    errors: List[str] = []
    if not plan.steps:
        errors.append("query_plan.steps must be non-empty")
        return errors
    ids = [s.id for s in plan.steps]
    if any(not i for i in ids):
        errors.append("each step must have a non-empty id")
    if len(ids) != len(set(ids)):
        errors.append("query_plan step ids must be unique")
    id_set = set(ids)
    if not plan.final_step:
        errors.append("query_plan.final_step is required")
    elif plan.final_step not in id_set:
        errors.append(f"query_plan.final_step {plan.final_step!r} is not a step id")

    for step in plan.steps:
        errors.extend(_validate_step(step, id_set))

    errors.extend(_cycle_errors(plan.steps))
    return errors


def _validate_step(step: PlanStep, id_set: set[str]) -> List[str]:
    errors: List[str] = []
    prefix = f"step {step.id!r}"
    if step.target not in VALID_TARGETS:
        errors.append(f"{prefix} has invalid target {step.target!r}")
    if not step.sql:
        errors.append(f"{prefix} sql is required")
    elif _MID_LIST_DISTINCT.search(step.sql):
        errors.append(
            f"{prefix} sql uses DISTINCT after a SELECT-list comma; "
            "write SELECT DISTINCT col_a, col_b (COUNT/STRING_AGG DISTINCT is ok)"
        )
    if not step.purpose:
        errors.append(f"{prefix} purpose is required")
    if not step.returns:
        errors.append(f"{prefix} returns contract is required")
    for col, contract in step.returns.items():
        if contract.type not in VALID_RETURN_TYPES:
            errors.append(f"{prefix} returns.{col} has invalid type {contract.type!r}")
        if contract.cardinality not in VALID_CARDINALITY:
            errors.append(
                f"{prefix} returns.{col} has invalid cardinality {contract.cardinality!r}"
            )
    for dep in step.depends_on:
        if dep not in id_set:
            errors.append(f"{prefix} depends_on unknown step {dep!r}")
        if dep == step.id:
            errors.append(f"{prefix} cannot depend on itself")
    for step_id, column in find_placeholders(step.sql):
        if step_id not in step.depends_on:
            errors.append(
                f"{prefix} placeholder {{{{{step_id}.{column}}}}} requires depends_on {step_id!r}"
            )
    return errors


def _cycle_errors(steps: List[PlanStep]) -> List[str]:
    graph = {s.id: list(s.depends_on) for s in steps}
    visiting: set[str] = set()
    seen: set[str] = set()

    def visit(node: str) -> bool:
        if node in seen:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dep in graph.get(node, []):
            if dep in graph and visit(dep):
                return True
        visiting.remove(node)
        seen.add(node)
        return False

    for node in graph:
        if visit(node):
            return ["query_plan has a dependency cycle"]
    return []


def validate_envelope(env: PlannerEnvelope) -> List[str]:
    errors: List[str] = []
    if env.answer_type not in VALID_ANSWER_TYPES:
        errors.append(f"Invalid answer_type: {env.answer_type}")
    if not env.question.strip():
        errors.append("question must be non-empty")

    if env.clarity_required:
        if env.answer is not None:
            errors.append("answer must be null when clarity_required is true")
        if not env.clarifying_question:
            errors.append("clarifying_question must be non-empty when clarity_required is true")
        if env.query_plan is not None:
            errors.append("query_plan must be null when clarity_required is true")
        if env.final_sql is not None:
            errors.append("final_sql must be null when clarity_required is true")
        if env.chart_applicable:
            errors.append("chart_applicable must be false when clarity_required is true")
        if env.chart_details is not None:
            errors.append("chart_details must be null when clarity_required is true")
        if env.result_template is not None:
            errors.append("result_template must be null when clarity_required is true")
        if env.timeframe_rationale is not None:
            errors.append("timeframe_rationale must be null when clarity_required is true")
        if env.suggestions:
            errors.append("suggestions must be empty when clarity_required is true")
        return errors

    if env.clarifying_question is not None:
        errors.append("clarifying_question must be null when clarity_required is false")

    if env.answer_type == "Awareness":
        if not env.answer:
            errors.append("answer is required for Awareness")
        if env.query_plan is not None:
            errors.append("query_plan must be null for Awareness")
        if env.final_sql is not None:
            errors.append("final_sql must be null for Awareness")
        if env.chart_applicable:
            errors.append("chart_applicable must be false for Awareness")
        if env.result_template is not None:
            errors.append("result_template must be null for Awareness")
        if env.timeframe_rationale is not None:
            errors.append("timeframe_rationale must be null for Awareness")
        return errors

    if env.answer is not None:
        errors.append("answer must be null for Sql/Metadata (use query_plan / final_sql)")
    if env.query_plan is None:
        errors.append("query_plan is required when clarity_required is false")
    else:
        errors.extend(_validate_plan(env.query_plan))
        if env.query_plan.final_step:
            final = env.query_plan.step_map().get(env.query_plan.final_step)
            if final and env.final_sql and env.final_sql.strip() != final.sql.strip():
                errors.append("final_sql must match query_plan.final_step sql")
            if final and not env.final_sql:
                env.final_sql = final.sql
    if not env.understanding:
        errors.append("understanding is required when clarity_required is false")
    if env.answer_type == "Sql" and not env.timeframe_rationale:
        errors.append("timeframe_rationale is required for Sql")
    if env.answer_type == "Metadata" and env.timeframe_rationale is not None:
        errors.append("timeframe_rationale must be null for Metadata")

    if not env.chart_applicable:
        if env.chart_details is not None:
            errors.append("chart_details must be null when chart_applicable is false")
    else:
        if env.chart_details is None:
            errors.append("chart_details is required when chart_applicable is true")
        else:
            if not env.chart_details.chart_type:
                errors.append("chart_details.chart_type is required")
            elif env.chart_details.chart_type not in VALID_CHART_TYPES:
                errors.append(f"Invalid chart_details.chart_type: {env.chart_details.chart_type}")
            if not env.chart_details.x_axis:
                errors.append("chart_details.x_axis must be a non-empty array")
            if not env.chart_details.y_axis:
                errors.append("chart_details.y_axis must be a non-empty array")

    return errors
