"""LLM1 Bedrock agent — analytical consultant (no SQL)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from analytics.llm1.parser import parse_and_validate
from analytics.models import AnalyticalExecutionPlan
from llm import client as bedrock

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "llm1-consultant.md"
_PROMPT_CACHE: Optional[str] = None


def load_llm1_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    if not _PROMPT_PATH.is_file():
        raise FileNotFoundError(f"LLM1 prompt not found: {_PROMPT_PATH}")
    _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def build_user_message(
    message: str,
    injection: Dict[str, Any],
    history: List[Dict[str, str]],
) -> str:
    # Strip resolver-only payload from what LLM1 sees
    public = {k: v for k, v in injection.items() if not k.startswith("_")}
    parts = [
        "## Runtime catalog (injected)",
        "```json",
        json.dumps(public, indent=2),
        "```",
        "",
        "## Conversation history",
    ]
    if history:
        for turn in history[-12:]:
            parts.append(f"{turn['role'].upper()}: {turn['content']}")
    else:
        parts.append("(none)")
    parts.extend(["", "## User message", message.strip()])
    return "\n".join(parts)


def run_llm1(
    message: str,
    injection: Dict[str, Any],
    history: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
) -> Tuple[AnalyticalExecutionPlan, str, dict]:
    system_prompt = system_prompt or load_llm1_prompt()
    user_content = build_user_message(message, injection, history)
    t0 = time.monotonic()
    result = bedrock.invoke(
        [{"role": "user", "content": user_content}],
        system_prompt,
        temperature=0.0,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = result.get("text", "")
    aep, errors = parse_and_validate(raw_text)
    if errors:
        logger.warning("LLM1 AEP validation warnings: %s", errors)
    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "model_id": result.get("model_id"),
        "validation_errors": errors,
        "latency_ms": latency_ms,
        # The exact prompt pair that produced this reply, for the consult log.
        "system_prompt": system_prompt,
        "assembled_user_message": user_content,
        "history_turns": len(history or []),
    }
    return aep, raw_text, usage
