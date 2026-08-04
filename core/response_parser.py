"""Parse and validate LLM JSON envelope."""

from __future__ import annotations

from typing import Any, List, Optional

from core.models import AgentEnvelope, ChartDetails
from llm.parsing import parse_json

VALID_ANSWER_TYPES = {"Sql", "Metadata", "Awareness"}
VALID_CHART_TYPES = {"line", "scatter", "bar"}


def normalize_clarifying_question(val: Any) -> Optional[List[str]]:
    if val is None:
        return None
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return None


def _pad_units(units: List[str], length: int) -> List[str]:
    if not units:
        return [""] * length
    padded = list(units)
    while len(padded) < length:
        padded.append("")
    return padded[:length]


def parse_chart_details(val: Any, legacy_chart_type: Any = None) -> Optional[ChartDetails]:
    if val is None:
        return None
    if not isinstance(val, dict):
        return None
    details = ChartDetails.from_dict(val, chart_type=legacy_chart_type or val.get("chart_type"))
    details.x_unit = _pad_units(details.x_unit, len(details.x_axis))
    details.y_unit = _pad_units(details.y_unit, len(details.y_axis))
    return details


def parse_envelope(raw_text: str) -> AgentEnvelope:
    data = parse_json(raw_text)
    chart_applicable = bool(data.get("chart_applicable", False))
    chart_details = parse_chart_details(data.get("chart_details"), legacy_chart_type=data.get("chart_type"))
    result_template_raw = data.get("result_template")
    result_template = (
        str(result_template_raw).strip()
        if result_template_raw is not None and str(result_template_raw).strip()
        else None
    )
    return AgentEnvelope(
        clarity_required=bool(data.get("clarity_required", False)),
        clarifying_question=normalize_clarifying_question(data.get("clarifying_question")),
        question=str(data.get("question", "")),
        answer_type=str(data.get("answer_type", "Sql")),
        assumption=[str(a) for a in (data.get("assumption") or [])],
        answer=data.get("answer") if data.get("answer") is not None else None,
        chart_applicable=chart_applicable,
        chart_details=chart_details,
        result_template=result_template,
    )


def validate_envelope(env: AgentEnvelope) -> List[str]:
    errors: List[str] = []
    if env.answer_type not in VALID_ANSWER_TYPES:
        errors.append(f"Invalid answer_type: {env.answer_type}")
    if env.clarity_required:
        if env.answer is not None:
            errors.append("answer must be null when clarity_required is true")
        if not env.clarifying_question:
            errors.append("clarifying_question must be non-empty array when clarity_required is true")
        if env.chart_applicable:
            errors.append("chart_applicable must be false when clarity_required is true")
        if env.chart_details is not None:
            errors.append("chart_details must be null when clarity_required is true")
        if env.result_template is not None:
            errors.append("result_template must be null when clarity_required is true")
    else:
        if env.clarifying_question is not None:
            errors.append("clarifying_question must be null when clarity_required is false")
        if env.answer is None:
            errors.append("answer must be set when clarity_required is false")
    if not env.question.strip():
        errors.append("question must be non-empty")

    if env.answer_type == "Awareness" and env.chart_applicable:
        errors.append("chart_applicable must be false for Awareness answer_type")
    if env.answer_type == "Awareness" and env.result_template is not None:
        errors.append("result_template must be null for Awareness answer_type")

    if not env.chart_applicable:
        if env.chart_details is not None:
            errors.append("chart_details must be null when chart_applicable is false")
    else:
        if env.chart_details is None:
            errors.append("chart_details is required when chart_applicable is true")
        else:
            if not env.chart_details.chart_type:
                errors.append("chart_details.chart_type is required when chart_applicable is true")
            elif env.chart_details.chart_type not in VALID_CHART_TYPES:
                errors.append(f"Invalid chart_details.chart_type: {env.chart_details.chart_type}")
            if not env.chart_details.x_axis:
                errors.append("chart_details.x_axis must be a non-empty array")
            if not env.chart_details.y_axis:
                errors.append("chart_details.y_axis must be a non-empty array")
            if len(env.chart_details.x_unit) != len(env.chart_details.x_axis):
                errors.append("chart_details.x_unit length must match x_axis length")
            if len(env.chart_details.y_unit) != len(env.chart_details.y_axis):
                errors.append("chart_details.y_unit length must match y_axis length")

    return errors
