"""In-memory chat history per session."""

from __future__ import annotations

from typing import Dict, List

_MAX_TURNS = 12
_history: Dict[str, List[dict]] = {}


def get_history(session_id: str) -> List[dict]:
    return list(_history.get(session_id, []))


def add_turn(session_id: str, role: str, content: str) -> None:
    bucket = _history.setdefault(session_id, [])
    bucket.append({"role": role, "content": content})
    if len(bucket) > _MAX_TURNS:
        _history[session_id] = bucket[-_MAX_TURNS:]


def clear(session_id: str) -> None:
    _history.pop(session_id, None)
