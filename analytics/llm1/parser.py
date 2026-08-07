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
    intent = (q.intent or "").lower()
    if intent in ("awareness", "capability", "help"):
        return errors
    if intent in ("metadata", "metadata_lookup", "metadata_query"):
        # Entity required only when the ask is entity-scoped (locations etc.)
        loc_mode = (q.location.mode or "").lower()
        if loc_mode == "metadata_query" and not q.entity.values:
            # May still be valid for "list my entities" if entity.mode is metadata_query
            if (q.entity.mode or "").lower() != "metadata_query":
                errors.append("metadata location lookup missing entity.values")
        return errors
    if not q.entity.values:
        errors.append("resolved plan missing entity.values")
    if not q.variable.values:
        errors.append("resolved plan missing variable.values")
    if not q.location.values and q.location.mode != "metadata_query":
        errors.append("resolved plan missing location.values")
    return errors


def parse_and_validate(raw_text: str) -> Tuple[AnalyticalExecutionPlan, List[str]]:
    aep = parse_aep(raw_text)
    return aep, validate_aep(aep)
