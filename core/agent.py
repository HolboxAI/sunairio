"""Single-call NL→SQL agent orchestration."""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from core.models import AgentEnvelope, SessionContext
from core.response_parser import parse_envelope, validate_envelope
from llm import client as bedrock
from llm.prompt_loader import build_user_message, load_system_prompt

logger = logging.getLogger(__name__)


def run_agent(
    question: str,
    session_context: SessionContext,
    history: List[Dict[str, str]],
) -> Tuple[AgentEnvelope, str, dict]:
    system_prompt = load_system_prompt()
    user_content = build_user_message(question, session_context, history)
    messages = [{"role": "user", "content": user_content}]
    result = bedrock.invoke(messages, system_prompt, temperature=0.0)
    raw_text = result.get("text", "")
    envelope = parse_envelope(raw_text)
    errors = validate_envelope(envelope)
    if errors:
        logger.warning("Envelope validation warnings: %s", errors)
    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "model_id": result.get("model_id"),
        "validation_errors": errors,
    }
    return envelope, raw_text, usage
