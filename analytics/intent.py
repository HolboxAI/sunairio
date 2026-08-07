"""Intent helpers for analytics consult / resolver."""

from __future__ import annotations

from typing import Optional


def normalize_intent(intent: Optional[str]) -> str:
    return (intent or "").strip().lower().replace(" ", "_")


def is_awareness(intent: Optional[str]) -> bool:
    return normalize_intent(intent) in ("awareness", "capability", "help")


def is_metadata(intent: Optional[str]) -> bool:
    return normalize_intent(intent) in ("metadata", "metadata_lookup", "metadata_query")


def needs_variable(intent: Optional[str]) -> bool:
    return not (is_awareness(intent) or is_metadata(intent))


def needs_entity(intent: Optional[str], *, entity_mode: Optional[str] = None) -> bool:
    if is_awareness(intent):
        return False
    if is_metadata(intent) and (entity_mode or "").lower() in ("metadata_query",):
        # Listing entities themselves — no single entity required
        return False
    return True
