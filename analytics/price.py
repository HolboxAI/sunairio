"""Historical market price detection for the resolver."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

_PRICE_TOKENS = re.compile(
    r"\b(?:lmp|price|pricing|market\s+price)\b",
    re.IGNORECASE,
)


def is_price_phrase(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    lower = text.lower().replace("-", "_").replace(" ", "_")
    if lower in (
        "da_lmp",
        "rt_lmp",
        "day_ahead_lmp",
        "real_time_lmp",
        "day_ahead",
        "real_time",
        "historical_price",
        "price",
        "lmp",
    ):
        return True
    return bool(_PRICE_TOKENS.search(text))


def parse_historical_price(raw: str) -> Optional[Dict[str, str]]:
    """Return price_column + display_name, or None if ambiguous/non-price."""
    text = (raw or "").strip()
    if not text:
        return None
    lower = text.lower().replace("-", " ")

    if "day" in lower and "ahead" in lower or lower in ("da", "da lmp", "da_lmp", "day_ahead"):
        return {"column": "day_ahead", "display_name": "Day-Ahead LMP", "unit": "$/MWh"}
    if ("real" in lower and "time" in lower) or lower in (
        "rt",
        "rt lmp",
        "rt_lmp",
        "real_time",
        "realtime",
    ):
        return {"column": "real_time", "display_name": "Real-Time LMP", "unit": "$/MWh"}

    if _PRICE_TOKENS.search(text):
        # Generic "price" / "lmp" — caller should clarify DA vs RT if needed.
        return None
    return None
