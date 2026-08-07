"""Token-level matching shared by the resolver stages.

Substring matching is deliberately avoided here: it silently maps "iso" onto
"ISONE" and "wind" onto "wind_speed_100m". Every match is made on whole tokens.
"""

from __future__ import annotations

import re
from typing import List


def tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def normalize(text: str) -> str:
    return " ".join(tokenize(text))


def contains_phrase(haystack: List[str], needle: List[str]) -> bool:
    """True when `needle` appears in `haystack` as a run of whole tokens."""
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[i : i + len(needle)] == needle
        for i in range(len(haystack) - len(needle) + 1)
    )


def phrase_overlap(a: str, b: str) -> bool:
    """True when either string contains the other as a whole-token phrase."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return False
    return contains_phrase(ta, tb) or contains_phrase(tb, ta)
