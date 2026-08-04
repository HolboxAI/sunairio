"""Compare injected session context against sunairio-sql-prompt.md §3 spec."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_REQUIRED_TOP_KEYS = {
    "username",
    "current_utc",
    "allowed_entities",
    "latest_inits",
    "conversation_state",
    "variable_units",
    "entity_catalog",
}
_REQUIRED_STATE_KEYS = {"entity_shortname", "location_key", "variable", "timeframe"}
_REQUIRED_ENTITY_KEYS = {"entity_id", "entity", "shortname", "timezone"}


def check_context_against_spec(ctx: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    missing = _REQUIRED_TOP_KEYS - set(ctx.keys())
    if missing:
        warnings.append(f"Missing top-level session keys: {sorted(missing)}")
    state = ctx.get("conversation_state") or {}
    missing_state = _REQUIRED_STATE_KEYS - set(state.keys())
    if missing_state:
        warnings.append(f"Missing conversation_state keys: {sorted(missing_state)}")
    for i, ent in enumerate(ctx.get("allowed_entities") or []):
        missing_ent = _REQUIRED_ENTITY_KEYS - set(ent.keys())
        if missing_ent:
            warnings.append(f"allowed_entities[{i}] missing: {sorted(missing_ent)}")
    inits = ctx.get("latest_inits") or {}
    for shortname in [e.get("shortname") for e in ctx.get("allowed_entities") or []]:
        if shortname and shortname not in inits:
            warnings.append(f"latest_inits missing entity shortname: {shortname}")
    return warnings


def summarize_for_console(request_id: str, ctx: Dict[str, Any]) -> str:
    entities = [e.get("shortname") for e in ctx.get("allowed_entities") or []]
    init_count = len(ctx.get("latest_inits") or {})
    return (
        f"request={request_id} entities={entities} latest_inits_entities={init_count} "
        f"user={ctx.get('username')}"
    )
