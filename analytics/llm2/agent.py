"""LLM2 Bedrock agent — SQL generation from a confirmed REP."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from analytics.llm2.parser import Llm2Plan, parse_and_validate
from analytics.llm2.schema_inject import build_schema_block
from llm import client as bedrock

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "llm2-sql.md"
_PROMPT_CACHE: Optional[str] = None


def load_llm2_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(f"LLM2 prompt not found: {_PROMPT_PATH}")
    _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def build_user_message(rep: Dict[str, Any]) -> str:
    schema_block = build_schema_block(rep)
    return "\n".join(
        [
            schema_block,
            "",
            "## Resolved execution plan (REP)",
            "```json",
            json.dumps(rep, indent=2, default=str),
            "```",
            "",
            "Generate the SQL JSON envelope that executes this plan.",
        ]
    )


def run_llm2(
    rep: Dict[str, Any],
    *,
    system_prompt: Optional[str] = None,
) -> Tuple[Llm2Plan, str, dict]:
    """Invoke LLM2. Returns (plan, raw_text, usage)."""
    system_prompt = system_prompt or load_llm2_prompt()
    user_content = build_user_message(rep)
    t0 = time.monotonic()
    result = bedrock.invoke(
        [{"role": "user", "content": user_content}],
        system_prompt,
        temperature=0.0,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = result.get("text", "")
    plan, errors = parse_and_validate(raw_text)
    if errors:
        logger.warning("LLM2 plan validation errors: %s", errors)
    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "model_id": result.get("model_id"),
        "validation_errors": errors,
        "latency_ms": latency_ms,
        "system_prompt": system_prompt,
        "assembled_user_message": user_content,
    }
    return plan, raw_text, usage
