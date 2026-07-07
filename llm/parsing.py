"""Robust JSON extraction from LLM responses."""

from __future__ import annotations

import json
import re


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("{"):
        try:
            return json.loads(text, strict=False)
        except json.JSONDecodeError:
            pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip(), strict=False)
        except json.JSONDecodeError:
            pass
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1], strict=False)
                except json.JSONDecodeError:
                    start = None
    raise ValueError(f"Could not parse JSON from LLM response: {text[:300]}")
