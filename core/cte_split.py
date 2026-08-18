"""Split mixed WITH/UNION SQL into Forecast vs Lake CTE subgraphs.

Used when the LLM writes several single-backend CTEs, UNIONs them, then
aggregates (month×hour, etc.). Each subgraph runs on its database; DuckDB
only merges the already-aggregated rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from core.materialize import extract_table_refs
from security.sql_guard import _iter_cte_definitions, _matching_paren, normalize_sql, split_union_all

_FROM_IDENT_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_RESERVED = frozenset({
    "SELECT", "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "UNION", "ON",
    "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "LATERAL", "VALUES",
})
_AVG_RE = re.compile(
    r"AVG\s*\(\s*(.*?)\s*\)\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class PartitionedCteUnion:
    forecast_sql: str
    lake_sql: str
    remainder_sql: str
    agg_cte_name: Optional[str]
    avg_aliases: List[str]


def parse_with(sql: str) -> Tuple[List[Tuple[str, str]], str]:
    text = normalize_sql(sql)
    ctes = _iter_cte_definitions(text)
    if not ctes:
        return [], text
    last_name = ctes[-1][0]
    matches = list(re.finditer(rf"\b{re.escape(last_name)}\s+AS\s*\(", text, re.IGNORECASE))
    if not matches:
        return ctes, text
    close = _matching_paren(text, matches[-1].end() - 1)
    if close is None:
        return ctes, text
    remainder = text[close + 1 :].strip()
    if remainder.startswith(","):
        remainder = remainder[1:].strip()
    return ctes, remainder


def _cte_refs(body: str, cte_names: Set[str]) -> List[str]:
    found: List[str] = []
    for match in _FROM_IDENT_RE.finditer(body):
        name = match.group(1)
        if name.upper() in _RESERVED:
            continue
        key = next((n for n in cte_names if n.lower() == name.lower()), None)
        if key and key not in found:
            found.append(key)
    return found


def _classify_ctes(ctes: Sequence[Tuple[str, str]]) -> Dict[str, str]:
    names = {n for n, _ in ctes}
    cte_map = {n: b for n, b in ctes}
    backends: Dict[str, str] = {}
    for name, body in ctes:
        kinds: Set[str] = set()
        for ref in extract_table_refs(body):
            kinds.add(ref.backend)
        for dep in _cte_refs(body, names):
            if dep in backends:
                kinds.add(backends[dep])
        if kinds == {"forecast"}:
            backends[name] = "forecast"
        elif kinds == {"lake"}:
            backends[name] = "lake"
        elif len(kinds) > 1:
            backends[name] = "mixed"
        else:
            backends[name] = "unknown"
    return backends


def _simple_from_ident(select_sql: str, cte_names: Set[str]) -> Optional[str]:
    match = _FROM_IDENT_RE.search(select_sql)
    if not match:
        return None
    name = match.group(1)
    if name.upper() in _RESERVED:
        return None
    return next((n for n in cte_names if n.lower() == name.lower()), None)


def _collect_deps(root: str, cte_map: Dict[str, str], names: Set[str]) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()

    def walk(node: str) -> None:
        if node in seen or node not in cte_map:
            return
        seen.add(node)
        for dep in _cte_refs(cte_map[node], names):
            walk(dep)
        ordered.append(node)

    walk(root)
    return ordered


def _split_select_items(items_sql: str) -> List[str]:
    items: List[str] = []
    depth = 0
    start = 0
    in_str = False
    i = 0
    n = len(items_sql)
    while i < n:
        ch = items_sql[i]
        if in_str:
            if ch == "'" and i + 1 < n and items_sql[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            items.append(items_sql[start:i].strip())
            start = i + 1
        i += 1
    tail = items_sql[start:].strip()
    if tail:
        items.append(tail)
    return items


def _split_as_alias(item: str) -> Tuple[str, Optional[str]]:
    match = re.search(r"\s+AS\s+((?:\"[^\"]+\")|[A-Za-z_][\w]*)\s*$", item, re.IGNORECASE)
    if match:
        return item[: match.start()].strip(), match.group(1)
    return item.strip(), None


def qualify_ambiguous_join_select(body: str) -> str:
    """Qualify SELECT MONTH from a JOIN of two CTEs that both expose month.

    Postgres rejects unqualified `SELECT month` when both join sides have it.
    """
    join_match = re.search(
        r"\bFROM\s+[A-Za-z_][\w]*\s+(?P<left>[A-Za-z_][\w]*)\s+"
        r"(?:(?:INNER|LEFT|RIGHT|FULL)\s+)?(?:OUTER\s+)?JOIN\s+"
        r"[A-Za-z_][\w]*\s+(?P<right>[A-Za-z_][\w]*)\s+ON\b",
        body,
        re.IGNORECASE,
    )
    if not join_match:
        return body
    left = join_match.group("left")
    right = join_match.group("right")
    on_sql = body[join_match.end() :]
    keys: List[str] = []
    for eq in re.finditer(
        rf"\b{re.escape(left)}\.([A-Za-z_][\w]*)\s*=\s*{re.escape(right)}\.([A-Za-z_][\w]*)\b",
        on_sql,
        re.IGNORECASE,
    ):
        if eq.group(1).lower() == eq.group(2).lower():
            keys.append(eq.group(1))
    if not keys:
        return body
    from_match = re.search(r"\bFROM\b", body, re.IGNORECASE)
    if not from_match:
        return body
    select_part = body[: from_match.start()]
    rest = body[from_match.start() :]
    head = re.match(r"(\s*SELECT\s+)(.*)$", select_part, re.IGNORECASE | re.DOTALL)
    if not head:
        return body
    keyset = {k.lower() for k in keys}
    rewritten_items = []
    for item in _split_select_items(head.group(2)):
        expr, alias = _split_as_alias(item)
        if re.fullmatch(r"[A-Za-z_][\w]*", expr) and expr.lower() in keyset:
            expr = f"{left}.{expr}"
        rewritten_items.append(f"{expr} AS {alias}" if alias else expr)
    return head.group(1) + ", ".join(rewritten_items) + " " + rest


def _render_with(names: Sequence[str], cte_map: Dict[str, str], final_select: str) -> str:
    parts = [f"{n} AS ({qualify_ambiguous_join_select(cte_map[n])})" for n in names]
    return "WITH " + ", ".join(parts) + " " + final_select.strip()


def _push_avgs(select_sql: str) -> Tuple[str, List[str]]:
    aliases: List[str] = []

    def _repl(match: re.Match) -> str:
        expr = match.group(1).strip()
        alias = match.group(2)
        aliases.append(alias)
        return (
            f"SUM({expr}) AS _sum__{alias}, COUNT(*) AS _n__{alias}"
        )

    rewritten = _AVG_RE.sub(_repl, select_sql)
    return rewritten, aliases


def _find_union_cte(
    ctes: Sequence[Tuple[str, str]],
    backends: Dict[str, str],
    names: Set[str],
) -> Optional[Tuple[str, str, str]]:
    """Return (union_cte, forecast_src, lake_src)."""
    for name, body in ctes:
        parts = split_union_all(body)
        if len(parts) != 2:
            continue
        left = _simple_from_ident(parts[0], names)
        right = _simple_from_ident(parts[1], names)
        if not left or not right:
            continue
        pair = {backends.get(left), backends.get(right)}
        if pair != {"forecast", "lake"}:
            continue
        forecast_src = left if backends[left] == "forecast" else right
        lake_src = right if backends[right] == "lake" else left
        return name, forecast_src, lake_src
    return None


def _find_agg_over_union(
    ctes: Sequence[Tuple[str, str]],
    remainder: str,
    union_name: str,
) -> Tuple[Optional[str], str]:
    """Return (agg_cte_name or None, select-with-group-by body)."""
    for name, body in ctes:
        if not re.search(rf"\bFROM\s+{re.escape(union_name)}\b", body, re.IGNORECASE):
            continue
        if not re.search(r"\bGROUP\s+BY\b", body, re.IGNORECASE):
            continue
        return name, body
    if re.search(rf"\bFROM\s+{re.escape(union_name)}\b", remainder, re.IGNORECASE) and re.search(
        r"\bGROUP\s+BY\b", remainder, re.IGNORECASE
    ):
        return None, remainder
    return None, ""


def try_partitioned_cte_union(sql: str) -> Optional[PartitionedCteUnion]:
    ctes, remainder = parse_with(sql)
    if len(ctes) < 2 or not remainder.upper().lstrip().startswith("SELECT"):
        return None
    names = {n for n, _ in ctes}
    cte_map = {n: b for n, b in ctes}
    backends = _classify_ctes(ctes)
    found = _find_union_cte(ctes, backends, names)
    if not found:
        return None
    union_name, forecast_src, lake_src = found
    agg_name, agg_sql = _find_agg_over_union(ctes, remainder, union_name)
    if not agg_sql:
        return None
    pushed, avg_aliases = _push_avgs(agg_sql)
    if not avg_aliases:
        return None

    forecast_select = re.sub(
        rf"\bFROM\s+{re.escape(union_name)}\b",
        f"FROM {forecast_src}",
        pushed,
        count=1,
        flags=re.IGNORECASE,
    )
    lake_select = re.sub(
        rf"\bFROM\s+{re.escape(union_name)}\b",
        f"FROM {lake_src}",
        pushed,
        count=1,
        flags=re.IGNORECASE,
    )
    forecast_select = re.sub(
        r"\s+ORDER\s+BY\b.*$",
        "",
        forecast_select,
        flags=re.IGNORECASE | re.DOTALL,
    )
    lake_select = re.sub(
        r"\s+ORDER\s+BY\b.*$",
        "",
        lake_select,
        flags=re.IGNORECASE | re.DOTALL,
    )

    f_deps = _collect_deps(forecast_src, cte_map, names)
    l_deps = _collect_deps(lake_src, cte_map, names)
    forecast_sql = _render_with(f_deps, cte_map, forecast_select)
    lake_sql = _render_with(l_deps, cte_map, lake_select)
    if agg_name:
        remainder_sql = remainder
    else:
        order_limit = ""
        order_match = re.search(r"\s+ORDER\s+BY\b.*$", remainder, re.IGNORECASE | re.DOTALL)
        if order_match:
            order_limit = order_match.group(0)
        remainder_sql = f'SELECT * FROM "_merged"{order_limit}'
        agg_name = "_merged"
    return PartitionedCteUnion(
        forecast_sql=forecast_sql,
        lake_sql=lake_sql,
        remainder_sql=remainder_sql,
        agg_cte_name=agg_name,
        avg_aliases=avg_aliases,
    )


def duckdb_merge_avgs(
    columns: Sequence[str],
    avg_aliases: Sequence[str],
    out_table: str,
) -> str:
    keys = [
        c
        for c in columns
        if not c.startswith("_sum__") and not c.startswith("_n__")
    ]
    key_list = ", ".join(f'"{k}"' for k in keys)
    avg_selects = []
    for alias in avg_aliases:
        avg_selects.append(
            f'SUM("_sum__{alias}") / NULLIF(SUM("_n__{alias}"), 0) AS "{alias}"'
        )
    select_list = key_list
    if avg_selects:
        select_list = key_list + ", " + ", ".join(avg_selects) if keys else ", ".join(avg_selects)
    group = f" GROUP BY {key_list}" if keys else ""
    return (
        f'CREATE TABLE "{out_table}" AS '
        f"SELECT {select_list} FROM ("
        f'SELECT * FROM "_forecast_part" UNION ALL SELECT * FROM "_lake_part"'
        f") _u{group}"
    )
