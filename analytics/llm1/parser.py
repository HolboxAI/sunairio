"""Parse and validate LLM1 Analytical Execution Plan JSON."""

from __future__ import annotations

from typing import List, Tuple

from analytics.models import AnalyticalExecutionPlan
from llm.parsing import parse_json


def parse_aep(raw_text: str) -> AnalyticalExecutionPlan:
    data = parse_json(raw_text)
    if not isinstance(data, dict):
        raise ValueError("LLM1 response is not a JSON object")
    return AnalyticalExecutionPlan.from_dict(data)


def validate_aep(aep: AnalyticalExecutionPlan) -> List[str]:
    errors: List[str] = []
    if aep.status not in ("clarification_required", "resolved"):
        errors.append(f"invalid status: {aep.status}")
    if aep.status == "clarification_required":
        if not aep.clarification_questions and not aep.assistant_message:
            errors.append("clarification_required without questions or message")
        return errors

    q = aep.query
    if not q.intent:
        errors.append("resolved plan missing query.intent")
    if not q.entity.values and q.intent not in ("awareness",):
        errors.append("resolved plan missing entity.values")
    if q.intent not in ("metadata", "awareness", "metadata_lookup"):
        if not q.variable.values:
            errors.append("resolved plan missing variable.values")
        if not q.location.values and q.location.mode != "metadata_query":
            errors.append("resolved plan missing location.values")
    return errors


def parse_and_validate(raw_text: str) -> Tuple[AnalyticalExecutionPlan, List[str]]:
    aep = parse_aep(raw_text)
    return aep, validate_aep(aep)
