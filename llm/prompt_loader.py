"""Load system prompt and build user messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from config.settings import settings
from core.models import SessionContext

_PROMPT_CACHE: str | None = None


def load_system_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    path = Path(settings.prompt_path)
    if not path.is_file():
        raise FileNotFoundError(f"System prompt not found: {path}")
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
