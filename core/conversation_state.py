"""Persist and update conversation slots across turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from core.models import AgentEnvelope, ConversationState
from data import app_db


def load(session_id: str) -> ConversationState:
    return ConversationState.from_dict(app_db.get_conversation_state(session_id))


def save(session_id: str, state: ConversationState) -> None:
    app_db.upsert_conversation_state(session_id, state.to_dict())


def clear(session_id: str) -> None:
    app_db.clear_conversation_state(session_id)


def update_from_envelope(session_id: str, envelope: AgentEnvelope) -> ConversationState:
    state = load(session_id)
    for item in envelope.assumption or []:
        low = item.lower()
        if "entity:" in low or "shortname" in low:
            m = re.search(r"([a-z0-9_]+_generic|duke|pjm_generic|ercot_generic)", low)
            if m:
                state.entity_shortname = m.group(1)
        if "location:" in low:
            m = re.search(r"location:\s*(\S+)", item, re.I)
            if m:
                state.location_key = m.group(1).strip("()")
        if "variable:" in low:
            m = re.search(r"variable:\s*(\S+)", item, re.I)
            if m:
                state.variable = m.group(1)
        if "timeframe:" in low:
            state.timeframe = item.split(":", 1)[-1].strip()
    save(session_id, state)
    return state


def merge_user_message_slots(question: str, state: ConversationState) -> ConversationState:
    return state
