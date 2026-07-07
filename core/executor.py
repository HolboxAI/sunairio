"""SQL execution router (phase 2 stub)."""

from __future__ import annotations

from security.sql_guard import classify_sql_target


def execute(sql: str) -> dict:
    raise NotImplementedError("SQL execution is phase 2; v1 returns LLM envelope only")


def route_sql(sql: str) -> str:
    return classify_sql_target(sql)
