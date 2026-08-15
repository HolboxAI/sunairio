"""Rewrite cross-DB threshold SQL to forecast-only using a resolved numeric threshold."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from security.sql_guard import has_historical_iso_table, normalize_sql


def rewrite_sql_with_bound_threshold(sql: str, threshold: float) -> str:
    """Drop historical CTE(s) and bind threshold alias references to a literal."""
    text = normalize_sql(sql)
    if not has_historical_iso_table(text):
        return text

    hist_ctes = _historical_cte_names(text)
    if not hist_ctes:
        return text

    out = text
    for cte_name in hist_ctes:
        cross = re.search(
            rf"\bCROSS\s+JOIN\s+{re.escape(cte_name)}\s+(\w+)\b",
            out,
            re.IGNORECASE,
        )
        if not cross:
            continue
        alias = cross.group(1)
        cols = set(
            re.findall(rf"\b{re.escape(alias)}\.(\w+)\b", out, flags=re.IGNORECASE)
        )
        if not cols:
            continue
        out = _remove_cte(out, cte_name)
        out = re.sub(
            rf"\s+CROSS\s+JOIN\s+{re.escape(cte_name)}\s+{re.escape(alias)}\s*",
            " ",
            out,
            count=1,
            flags=re.IGNORECASE,
        )
        literal = _format_threshold(threshold)
        for col in cols:
            out = re.sub(
                rf"\b{re.escape(alias)}\.{re.escape(col)}\b",
                literal,
                out,
                flags=re.IGNORECASE,
            )
        out = _strip_constant_from_group_by(out, literal)
    return _cleanup_with(out)


def _strip_constant_from_group_by(sql: str, literal: str) -> str:
    """Remove a bound threshold literal from GROUP BY lists."""

    def _clean_group(match: re.Match[str]) -> str:
        clause = match.group(1)
        parts = [p.strip() for p in clause.split(",") if p.strip()]
        kept = [p for p in parts if p != literal]
        if not kept:
            return match.group(0)
        return "GROUP BY " + ", ".join(kept)

    return re.sub(
        r"GROUP\s+BY\s+([^;]+?)(?=\s+ORDER\s+BY|\s+LIMIT|\s+HAVING|\s*$)",
        _clean_group,
        sql,
        flags=re.IGNORECASE,
    )


def _format_threshold(threshold: float) -> str:
    if abs(threshold - round(threshold)) < 1e-9:
        return str(int(round(threshold)))
    return repr(threshold)


def _historical_cte_names(sql: str) -> List[str]:
    names: List[str] = []
    for name, body in _iter_cte_definitions(sql):
        if has_historical_iso_table(body):
            names.append(name)
    return names


def _iter_cte_definitions(sql: str) -> List[Tuple[str, str]]:
    text = normalize_sql(sql)
    if not re.match(r"WITH\s", text, re.IGNORECASE):
        return []

    i = re.match(r"WITH\s+", text, re.IGNORECASE).end()
    ctes: List[Tuple[str, str]] = []
    while i < len(text):
        name_match = re.match(r"(\w+)\s+AS\s*\(", text[i:], re.IGNORECASE)
        if not name_match:
            break
        name = name_match.group(1)
        start = i + name_match.end()
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        if depth != 0:
            break
        body = text[start : j - 1].strip()
        ctes.append((name, body))
        i = j
        rest = text[i:].lstrip()
        if rest.startswith(","):
            i = j + (len(text[i:]) - len(rest)) + 1
            continue
        break
    return ctes


def _remove_cte(sql: str, cte_name: str) -> str:
    text = normalize_sql(sql)
    pattern = (
        rf"(?P<prefix>WITH\s+)?(?P<lead>,?\s*){re.escape(cte_name)}\s+AS\s*\("
    )
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return text

    start = match.start()
    open_paren = match.end() - 1
    depth = 1
    j = open_paren + 1
    while j < len(text) and depth > 0:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
        j += 1
    end = j
    tail = text[end:].lstrip()
    if tail.startswith(","):
        end += 1
        tail = tail[1:].lstrip()
    head = text[:start]
    if match.group("prefix"):
        if tail.upper().startswith("SELECT"):
            return tail
        return f"WITH {tail}"
    return f"{head}{tail}".strip()


def _cleanup_with(sql: str) -> str:
    text = normalize_sql(sql)
    text = re.sub(r"WITH\s+,", "WITH ", text, flags=re.IGNORECASE)
    text = re.sub(r",\s*,", ", ", text)
    return text.strip()
