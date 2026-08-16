"""Planner v3 API — query plan + lookup binding + final SQL. Isolated from v1/v2."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app import auth, sessions
from app.api.schemas import (
    ChartDetails,
    ClearRequest,
    HistorySessionItem,
    HistorySessionListResponse,
    HistorySessionTitleUpdate,
    HistoryThreadResponse,
    LlmUsage,
    PlannerExecuteRequest,
    PlannerExecuteResponse,
    PlannerQueryRequest,
    PlannerQueryResponse,
    QueryData,
)
from app.deps import get_current_user, new_request_id
from core import conversation_state
from core.chart_units import enrich_chart_units, resolve_query_timezone
from core.result_summary import build_metadata_answer, build_result_summary
from core.session_context import build_session_context, to_prompt_json
from data import app_db
from observability import llm_audit_log, prompt_diff
from planner import agent as planner_agent
from planner import session_store as planner_sessions
from planner.adapter import as_agent_envelope
from planner.executor import PlanExecutionError, execute_plan
from planner.models import QueryPlan
from planner.placeholders import UnresolvedPlaceholderError
from planner.prompt_loader import build_user_message, load_system_prompt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v3", tags=["planner"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_token_limit(user: dict) -> None:
    user_id = user.get("id") or 0
    if user.get("role") == "admin" or not user_id:
        return
    allowed, limit_msg, usage_summary = app_db.check_token_limit(user_id)
    if not allowed:
        status_code = 429 if usage_summary else 403
        detail: dict = {"message": limit_msg}
        if usage_summary:
            detail["usage"] = usage_summary
        raise HTTPException(status_code=status_code, detail=detail)


def _bind_lookups(envelope, request_id: str, acl) -> None:
    if envelope.clarity_required or envelope.query_plan is None:
        return
    try:
        plan, _result, _vals = execute_plan(
            envelope.query_plan,
            request_id=request_id,
            acl=acl,
            skip_final=True,
        )
        envelope.query_plan = plan
        final = plan.step_map().get(plan.final_step)
        if final and final.bound_sql:
            envelope.final_sql = final.bound_sql
    except (PlanExecutionError, UnresolvedPlaceholderError, ValueError) as e:
        logger.info("Planner lookup binding failed (%s): %s", request_id, e)
        envelope.lookup_error = str(e)


@router.post("/query", response_model=PlannerQueryResponse)
def query(req: PlannerQueryRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_id = req.session_id or request_id
    request_time = _utc_now_iso()
    t0 = time.monotonic()

    _check_token_limit(user)
    planner_sessions.ensure_tables()
    user_id = int(user.get("id") or 0)
    if user_id and not planner_sessions.touch_session(session_id, user_id):
        raise HTTPException(status_code=403, detail="Session belongs to another user")

    acl = auth.get_acl_for_user(user)
    state = conversation_state.load(session_id)
    state = conversation_state.merge_user_message_slots(req.question, state)
    ctx = build_session_context(user, acl, state)
    ctx_dict = to_prompt_json(ctx)
    context_warnings = prompt_diff.check_context_against_spec(ctx_dict)

    history = planner_sessions.get_history(session_id) if user_id else sessions.get_history(session_id)
    system_prompt = load_system_prompt()
    assembled_user_message = build_user_message(req.question, ctx, history)

    llm_audit_log.log_llm_request(
        request_id,
        {
            "request_id": request_id,
            "user_id": user.get("id"),
            "session_id": session_id,
            "channel": "planner_v3",
            "model_id": None,
            "system_prompt_hash": llm_audit_log.prompt_hash(system_prompt),
            "system_prompt": system_prompt,
            "assembled_user_message": assembled_user_message,
            "session_context": ctx_dict,
            "user_message": req.question,
            "chat_history": history,
            "context_warnings": context_warnings,
        },
    )

    envelope, raw_text, usage = planner_agent.run_planner(
        req.question,
        ctx,
        history,
        system_prompt=system_prompt,
        user_content=assembled_user_message,
    )
    context_warnings = list(context_warnings) + list(usage.get("validation_errors") or [])

    v1_env = as_agent_envelope(envelope)
    response_timezone = resolve_query_timezone(ctx.allowed_entities, state, v1_env)
    enrich_chart_units(v1_env, state, timezone=response_timezone)
    envelope.chart_details = v1_env.chart_details

    _bind_lookups(envelope, request_id, acl)

    latency_ms = int((time.monotonic() - t0) * 1000)
    llm_audit_log.log_llm_response(
        request_id,
        {
            "raw_model_text": raw_text,
            "parsed_envelope": envelope.to_dict(),
            "validation_errors": usage.get("validation_errors", []),
            "execution_summary": {"lookup_error": envelope.lookup_error},
            "token_usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "model_id": usage.get("model_id"),
            },
            "latency_ms": latency_ms,
        },
    )
    audit_path = llm_audit_log.write_audit_bundle(request_id)

    conversation_state.update_from_envelope(session_id, v1_env)
    planner_sessions.add_turn(session_id, "user", req.question)
    assistant_text = envelope.understanding or envelope.question or envelope.answer or ""
    planner_sessions.add_turn(session_id, "assistant", assistant_text, envelope=envelope.to_dict())
    if not user_id:
        sessions.add_turn(session_id, "user", req.question)
        if assistant_text:
            sessions.add_turn(session_id, "assistant", assistant_text)

    chart_details_response = None
    if envelope.chart_details:
        chart_details_response = ChartDetails(**envelope.chart_details.to_dict())

    response = PlannerQueryResponse(
        request_id=request_id,
        session_id=session_id,
        request_time=request_time,
        response_time=_utc_now_iso(),
        clarity_required=envelope.clarity_required,
        clarifying_question=envelope.clarifying_question,
        question=envelope.question,
        original_question=req.question,
        understanding=envelope.understanding,
        answer_type=envelope.answer_type,
        assumptions=envelope.assumptions,
        suggestions=envelope.suggestions,
        answer=envelope.answer,
        query_plan=envelope.query_plan.to_dict() if envelope.query_plan else None,
        final_sql=envelope.final_sql,
        result_template=envelope.result_template,
        chart_applicable=envelope.chart_applicable,
        chart_details=chart_details_response,
        timezone=response_timezone,
        lookup_error=envelope.lookup_error,
        context_warnings=context_warnings,
        llm_usage=LlmUsage(
            model_id=usage.get("model_id") or "",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        ),
    )

    if user_id:
        try:
            app_db.save_query_history(
                user_id,
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "request_time": request_time,
                    "response_time": response.response_time,
                    "clarity_required": envelope.clarity_required,
                    "clarifying_question": envelope.clarifying_question,
                    "question": envelope.question,
                    "original_question": req.question,
                    "answer_type": envelope.answer_type,
                    "assumption": envelope.assumptions,
                    "answer": envelope.final_sql or envelope.answer,
                    "chart_applicable": envelope.chart_applicable,
                    "chart_details": envelope.chart_details.to_dict() if envelope.chart_details else None,
                    "timezone": response_timezone,
                    "result_summary": None,
                    "context_warnings": context_warnings,
                    "llm_usage": {
                        "model_id": usage.get("model_id") or "",
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                    },
                },
                link_conversation=False,
            )
        except Exception as e:
            logger.warning("Failed to persist planner usage history: %s", e)
    if audit_path:
        app_db.log_llm_audit_index(request_id, audit_path)

    return response


@router.post("/execute", response_model=PlannerExecuteResponse)
def execute_query(req: PlannerExecuteRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    request_time = _utc_now_iso()
    from core import executor as core_executor

    acl = auth.get_acl_for_user(user)
    sql = (req.sql or "").strip()
    plan = QueryPlan.from_dict(req.query_plan) if req.query_plan else None
    executed_sql = sql
    plan_dict = None
    raw_result = None

    from planner.placeholders import has_placeholders

    try:
        if sql and not has_placeholders(sql):
            raw_result, _detail = core_executor.execute_with_detail(sql, request_id, acl)
            executed_sql = sql
            if plan is not None:
                plan_dict = plan.to_dict()
        elif plan is not None:
            updated, raw_result, _vals = execute_plan(plan, request_id, acl, skip_final=False)
            plan_dict = updated.to_dict()
            final = updated.step_map().get(updated.final_step)
            executed_sql = (final.bound_sql if final else None) or sql or (final.sql if final else "")
            if raw_result is None:
                raise ValueError("Final query step produced no result")
        else:
            raise HTTPException(status_code=400, detail="sql or query_plan is required")
    except HTTPException:
        raise
    except ValueError as e:
        logger.info("Planner execute rejected (%s): %s", request_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.warning("Planner execute failed (%s): %s", request_id, e)
        raise HTTPException(status_code=500, detail=f"Execution failed: {e}") from e

    query_data = QueryData(**raw_result)
    response_answer = None
    result_summary = None
    if req.answer_type == "Metadata":
        response_answer = build_metadata_answer(
            question=req.question or "",
            columns=raw_result.get("columns"),
            rows=raw_result.get("rows"),
        )
    else:
        result_summary = build_result_summary(
            question=req.question or "",
            result_template=req.result_template,
            columns=raw_result.get("columns"),
            rows=raw_result.get("rows"),
        )

    return PlannerExecuteResponse(
        request_id=request_id,
        request_time=request_time,
        response_time=_utc_now_iso(),
        sql=executed_sql,
        data=query_data,
        result_summary=result_summary,
        answer=response_answer,
        query_plan=plan_dict,
        context_warnings=[],
    )


@router.get("/history", response_model=HistorySessionListResponse)
def list_history(user: dict = Depends(get_current_user), limit: int = Query(100, ge=1, le=500)):
    planner_sessions.ensure_tables()
    user_id = user.get("id") or 0
    if not user_id:
        return HistorySessionListResponse(items=[])
    items = planner_sessions.list_sessions(user_id, limit=limit)
    return HistorySessionListResponse(items=[HistorySessionItem(**item) for item in items])


@router.get("/history/thread", response_model=HistoryThreadResponse)
def history_thread(session_id: str = Query(...), user: dict = Depends(get_current_user)):
    planner_sessions.ensure_tables()
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    turns = planner_sessions.get_thread(user_id, session_id)
    title = planner_sessions.get_title(user_id, session_id)
    return HistoryThreadResponse(session_id=session_id, title=title, turns=turns)


@router.post("/history/hydrate", response_model=HistoryThreadResponse)
def hydrate_history(req: dict, user: dict = Depends(get_current_user)):
    planner_sessions.ensure_tables()
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session_id = str((req or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    turns = planner_sessions.get_thread(user_id, session_id)
    title = planner_sessions.get_title(user_id, session_id)
    if not turns:
        raise HTTPException(status_code=404, detail="Session not found")
    return HistoryThreadResponse(session_id=session_id, title=title, turns=turns)


@router.patch("/history/sessions/{session_id}", response_model=HistorySessionItem)
def update_session_title(
    session_id: str,
    req: HistorySessionTitleUpdate,
    user: dict = Depends(get_current_user),
):
    planner_sessions.ensure_tables()
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    updated = planner_sessions.update_title(user_id, session_id, req.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found or title invalid")
    return HistorySessionItem(**updated)


@router.delete("/history/sessions/{session_id}")
def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    planner_sessions.ensure_tables()
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    deleted = planner_sessions.delete_session(user_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "session_id": session_id}


@router.post("/query/clear")
def clear_session(req: ClearRequest, user: dict = Depends(get_current_user)):
    sessions.clear(req.session_id)
    conversation_state.clear(req.session_id)
    return {"ok": True, "session_id": req.session_id}
