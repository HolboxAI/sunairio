"""Load the v3 planner system prompt without touching the v1 prompt cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from config.settings import settings
from core.models import SessionContext

_PROMPT_CACHE: str | None = None


def planner_prompt_path() -> Path:
    return Path(settings.prompt_path).resolve().parent / "sunairio-planner-prompt.md"


def load_system_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    path = planner_prompt_path()
    if not path.is_file():
        raise FileNotFoundError(f"Planner system prompt not found: {path}")
    _PROMPT_CACHE = path.read_text(encoding="utf-8")
    return _PROMPT_CACHE


def build_user_message(
    question: str,
    session_context: SessionContext,
    history: List[Dict[str, str]],
) -> str:
    parts = [
        "## Session context (injected at runtime)",
        "```json",
        json.dumps(session_context.to_dict(), indent=2),
        "```",
        "",
        "## Conversation history",
    ]
    if history:
        for turn in history[-6:]:
            parts.append(f"{turn['role'].upper()}: {turn['content']}")
    else:
        parts.append("(none)")
    parts.extend(["", "## User question", question.strip()])
    return "\n".join(parts)
