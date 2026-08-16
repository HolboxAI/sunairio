"""Adapt planner envelopes to v1 helpers (conversation state, chart units)."""

from __future__ import annotations

from core.models import AgentEnvelope
from planner.models import PlannerEnvelope


def as_agent_envelope(env: PlannerEnvelope) -> AgentEnvelope:
    return AgentEnvelope(
        clarity_required=env.clarity_required,
        clarifying_question=env.clarifying_question,
        question=env.question,
        answer_type=env.answer_type,  # type: ignore[arg-type]
        assumption=list(env.assumptions),
        suggestions=list(env.suggestions),
        answer=env.final_sql or env.answer,
        chart_applicable=env.chart_applicable,
        chart_details=env.chart_details,
        result_template=env.result_template,
    )
