"""Session working memory injected into LLM1 prompts."""

from __future__ import annotations

import json
import re
from analytics.ambiguity import slots_from_refs

_METHODOLOGY_ASK = re.compile(
    r"(how\s+(?:will|would|are|do|going\s+to)\s+(?:you\s+)?(?:calculat|comput|derive|get|make)|"
    r"how\s+are\s+you\s+going\s+to\s+(?:calculat|comput|derive|get|make)|"
    r"explain\s+(?:how|the\s+calculation)|"
    r"what\s+(?:method|formula|approach)|"
    r"how\s+(?:is|are)\s+(?:this|that|it)\s+(?:calculat|comput))",
    re.IGNORECASE,
)

_PRICE_RT = re.compile(
    r"\b(?:real[\s-]?time|rt)\s*(?:lmp|price)?\b|\brt_lmp\b",
    re.IGNORECASE,
)
_PRICE_DA = re.compile(
    r"\b(?:day[\s-]?ahead|da)\s*(?:lmp|price)?\b|\bda_lmp\b",
    re.IGNORECASE,
)

# Relative timeframe phrases LLM1 may invent — normalize to resolver expressions.
_TIMEFRAME_ALIASES = {
    "next_week_monday_to_sunday": "next_week",
    "next_week_starting_monday": "next_week",
    "following_week_monday_sunday": "next_week",
    "week_starting_monday": "next_week",
}


def looks_like_methodology_question(message: str) -> bool:
    return bool(_METHODOLOGY_ASK.search(message or ""))


def looks_like_methodology_explanation(text: str) -> bool:
    """True when assistant/LLM1 text already explains the calculation."""
    lower = (text or "").lower()
    if len(lower) < 60:
        return False
    signals = (
        "sort the 1000",
        "ensemble path",
        "drop the lowest",
        "drop the outer",
        "trimmed mean",
        "middle 80",
        "for each hour",
        "arithmetic mean across",
        "percentile",
        "probability",
        "i'll calculate",
        "i will calculate",
        "here's how",
        "here is how",
    )
    return any(s in lower for s in signals)


def infer_resolved_slots(history: List[Dict[str, str]]) -> Dict[str, str]:
    """Best-effort recovery of decisions already made in the thread."""
    slots: Dict[str, str] = {}
    for turn in reversed(history or []):
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        lower = content.lower()
        if "price_type" not in slots:
            if _PRICE_RT.search(lower):
                slots["price_type"] = "real_time LMP"
            elif _PRICE_DA.search(lower):
                slots["price_type"] = "day_ahead LMP"
        if "entity" not in slots:
            for iso in ("PJM", "ERCOT", "MISO", "ISONE"):
                if iso.lower() in lower and turn.get("role") == "user":
                    slots["entity"] = iso
                    break
        if "location_scope" not in slots and "rto" in lower:
            slots["location_scope"] = "RTO (system-wide)"
    return slots


def format_refs_for_prompt(refs: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "").strip()
        key = str(ref.get("key") or "").strip()

        if kind == "location_threshold_table":
            rows = ref.get("rows") or []
            metric = ref.get("metric") or "threshold"
            entity = ref.get("entity") or ""
            parts = [f"- **{entity or 'Session'} location thresholds** ({metric}, ref: `{key}`):"]
            for row in rows[:8]:
                if not isinstance(row, dict):
                    continue
                name = row.get("location_name") or row.get("location_id") or "?"
                val = row.get("value")
                unit = row.get("unit") or ""
                val_txt = f"{val:,.2f}" if isinstance(val, float) else str(val)
                parts.append(f"  - {name}: {val_txt} {unit}".strip())
            if len(rows) > 8:
                parts.append(f"  - … and {len(rows) - 8} more locations")
            lines.extend(parts)
            continue

        if kind == "session_slot":
            slot_key = ref.get("slot_key") or key
            lines.append(
                f"- Resolved **{slot_key}**: {ref.get('value')} (do not re-ask unless user changes scope)"
            )
            continue

        value = ref.get("value")
        unit = str(ref.get("unit") or "").strip()
        label = str(ref.get("variable_label") or ref.get("kind") or key).strip()
        if value is None:
            continue
        val_txt = f"{value:,.2f}" if isinstance(value, float) else str(value)
        if unit:
            val_txt = f"{val_txt} {unit}"
        lines.append(f"- {label}: **{val_txt}** (ref: `{key}`)")
    return lines


def build_session_context_block(
    *,
    refs: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, str]]] = None,
    resolved_slots: Optional[Dict[str, str]] = None,
) -> str:
    """Markdown block appended to LLM1 user message."""
    parts: List[str] = ["## Session context"]
    has_content = False

    ref_lines = format_refs_for_prompt(list(refs or []))
    if ref_lines:
        has_content = True
        parts.append("**Computed references from earlier in this conversation:**")
        parts.extend(ref_lines)

    slots = dict(resolved_slots or {})
    slots.update(slots_from_refs(list(refs or [])))
    slots.update(infer_resolved_slots(list(history or [])))
    if slots:
        has_content = True
        parts.append("**Previously resolved (do not re-ask unless the user changes scope):**")
        for k, v in sorted(slots.items()):
            parts.append(f"- {k.replace('_', ' ')}: {v}")

    tf_notes = []
    for alias, canonical in _TIMEFRAME_ALIASES.items():
        tf_notes.append(f"`{alias}` → `{canonical}`")
    if tf_notes:
        has_content = True
        parts.append("**Relative timeframe aliases:** " + "; ".join(tf_notes[:4]))

    if not has_content:
        return ""
    return "\n".join(parts)


def normalize_timeframe_expression(expression: str) -> str:
    key = (expression or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _TIMEFRAME_ALIASES.get(key, expression)


def load_session_refs(session_id: str, user_id: int) -> List[Dict[str, Any]]:
    from analytics.session_store import list_references

    return list_references(session_id, user_id) or []
