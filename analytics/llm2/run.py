"""Orchestrate LLM2 generate → validate → execute for a confirmed REP."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from analytics.llm2 import agent as llm2_agent
from analytics.llm2.executor import (
    AnalyticsExecuteError,
    execute_plan,
    fill_result_template,
    format_answer_message,
)
from analytics.llm2.parser import Llm2Plan
from analytics.threshold_resolve import resolve_historical_threshold
from analytics.zero_row import diagnose_zero_rows

logger = logging.getLogger(__name__)


def run_confirmed_plan(
    rep: Dict[str, Any],
    *,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Full post-confirm pipeline.

    Returns a dict with keys:
      ok, message, sql, target, data, execution, llm_usage, plan, errors
    """
    try:
        rep, _threshold = resolve_historical_threshold(rep, request_id=request_id)
    except Exception as e:
        logger.exception("Historical threshold resolution failed (%s)", request_id)
        return {
            "ok": False,
            "message": f"Could not resolve historical threshold: {e}",
            "sql": None,
            "target": None,
            "data": None,
            "execution": None,
            "llm_usage": None,
            "plan": None,
            "errors": [str(e)],
            "raw_text": None,
        }

    try:
        plan, raw_text, usage = llm2_agent.run_llm2(rep)
    except Exception as e:
        logger.exception("LLM2 invoke failed (%s)", request_id)
        return {
            "ok": False,
            "message": f"SQL generation failed: {e}",
            "sql": None,
            "target": None,
            "data": None,
            "execution": None,
            "llm_usage": None,
            "plan": None,
            "errors": [str(e)],
            "raw_text": None,
        }

    validation_errors = list(usage.get("validation_errors") or [])
    debug = _llm2_debug(usage)
    if validation_errors:
        return {
            "ok": False,
            "message": "SQL generation produced an invalid plan: "
            + "; ".join(validation_errors),
            "sql": plan.sql,
            "target": plan.target,
            "data": None,
            "execution": None,
            "llm_usage": _usage_public(usage),
            "plan": plan.to_dict(),
            "errors": validation_errors,
            "raw_text": raw_text,
            "llm2_debug": debug,
        }

    if plan.target == "unsupported" or not plan.sql:
        reason = "; ".join(plan.assumptions) or (
            "This analysis needs Lake or cross-database SQL, "
            "which is not enabled yet."
        )
        return {
            "ok": False,
            "message": reason,
            "sql": None,
            "target": plan.target,
            "data": None,
            "execution": {"lake": "todo", "cross_db": "todo"},
            "llm_usage": _usage_public(usage),
            "plan": plan.to_dict(),
            "errors": ["unsupported"],
            "raw_text": raw_text,
            "llm2_debug": debug,
        }

    try:
        result, detail = execute_plan(plan, request_id=request_id, rep=rep)
    except AnalyticsExecuteError as e:
        return {
            "ok": False,
            "message": str(e),
            "sql": plan.sql,
            "target": plan.target,
            "data": None,
            "execution": {"lake": "todo", "cross_db": "todo"},
            "llm_usage": _usage_public(usage),
            "plan": plan.to_dict(),
            "errors": [str(e)],
            "raw_text": raw_text,
            "llm2_debug": debug,
        }
    except Exception as e:
        logger.exception("Analytics SQL execution failed (%s)", request_id)
        return {
            "ok": False,
            "message": f"Query execution failed: {e}",
            "sql": plan.sql,
            "target": plan.target,
            "data": None,
            "execution": None,
            "llm_usage": _usage_public(usage),
            "plan": plan.to_dict(),
            "errors": [str(e)],
            "raw_text": raw_text,
            "llm2_debug": debug,
        }

    filled = fill_result_template(plan.result_template, result)
    row_count = int(result.get("row_count") or len(result.get("rows") or []))
    if row_count == 0:
        hints = diagnose_zero_rows(rep, sql=plan.sql)
        plan.notes = list(plan.notes or []) + hints
    message = format_answer_message(
        template_filled=filled,
        result=result,
        plan=plan,
    )
    return {
        "ok": True,
        "message": message,
        "sql": plan.sql,
        "target": detail.get("backend") or plan.target,
        "data": {
            "columns": result.get("columns") or [],
            "rows": result.get("rows") or [],
            "row_count": result.get("row_count") or 0,
            "truncated": bool(result.get("truncated")),
            "backend": result.get("backend"),
            "query_time_ms": result.get("query_time_ms"),
        },
        "execution": detail,
        "llm_usage": _usage_public(usage),
        "plan": plan.to_dict(),
        "errors": [],
        "raw_text": raw_text,
        "llm2_debug": debug,
        "result_summary": filled,
    }


def _llm2_debug(usage: dict) -> dict:
    return {
        "system_prompt": usage.get("system_prompt"),
        "assembled_user_message": usage.get("assembled_user_message"),
        "validation_errors": usage.get("validation_errors") or [],
        "latency_ms": usage.get("latency_ms"),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "model_id": usage.get("model_id"),
    }


def _usage_public(usage: dict) -> dict:
    return {
        "model_id": usage.get("model_id") or "",
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
    }
