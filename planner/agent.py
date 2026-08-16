"""Single-call analytical query planner."""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from core.models import SessionContext
from llm import client as bedrock
from planner.models import PlannerEnvelope
from planner.parser import parse_envelope, validate_envelope
from planner.prompt_loader import build_user_message, load_system_prompt

logger = logging.getLogger(__name__)

PLANNER_MAX_TOKENS = 8192


def run_planner(
    question: str,
    session_context: SessionContext,
    history: List[Dict[str, str]],
    system_prompt: str | None = None,
    user_content: str | None = None,
) -> Tuple[PlannerEnvelope, str, dict]:
    system_prompt = system_prompt or load_system_prompt()
    user_content = user_content or build_user_message(
        question, session_context, history
    )
    messages = [{"role": "user", "content": user_content}]
    result = bedrock.invoke(
        messages,
        system_prompt,
        temperature=0.0,
        max_tokens=PLANNER_MAX_TOKENS,
    )
    raw_text = result.get("text", "")
    envelope = parse_envelope(raw_text)
    errors = validate_envelope(envelope)
    if errors:
        logger.warning("Planner envelope validation warnings: %s", errors)
    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "model_id": result.get("model_id"),
        "validation_errors": errors,
    }
    return envelope, raw_text, usage
