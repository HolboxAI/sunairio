"""Query endpoint — NL to LLM envelope; SQL execution is user-triggered via /query/execute."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app import auth, sessions
from app.api.schemas import (
    ChartDetails,
    ClearRequest,
    ExecuteQueryRequest,
    ExecuteQueryResponse,
    HistoryHydrateRequest,
    HistorySessionItem,
    HistorySessionListResponse,
    HistorySessionTitleUpdate,
    HistoryThreadResponse,
    LlmUsage,
    QueryData,
    QueryRequest,
    QueryResponse,
    SqlRequest,
    SqlResponse,
)
from app.deps import get_current_user, new_request_id
from core import agent, conversation_state, executor
from core.chart_units import enrich_chart_units, resolve_query_timezone
from core.result_summary import build_metadata_answer, build_result_summary
from core.session_context import build_session_context, to_prompt_json
from data import app_db
from llm.prompt_loader import build_user_message, load_system_prompt
from observability import llm_audit_log, prompt_diff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["query"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_id = req.session_id or request_id
    request_time = _utc_now_iso()
    t0 = time.monotonic()

    user_id = user.get("id") or 0
    if user.get("role") != "admin" and user_id:
        allowed, limit_msg, usage_summary = app_db.check_token_limit(user_id)
        if not allowed:
            status_code = 429 if usage_summary else 403
            detail: dict = {"message": limit_msg}
            if usage_summary:
                detail["usage"] = usage_summary
            raise HTTPException(status_code=status_code, detail=detail)

    acl = auth.get_acl_for_user(user)
    state = conversation_state.load(session_id)
    state = conversation_state.merge_user_message_slots(req.question, state)
    ctx = build_session_context(user, acl, state)
    ctx_dict = to_prompt_json(ctx)
    context_warnings = prompt_diff.check_context_against_spec(ctx_dict)
    logger.info(prompt_diff.summarize_for_console(request_id, ctx_dict))

    history = sessions.get_history(session_id)
    system_prompt = load_system_prompt()
    assembled_user_message = build_user_message(req.question, ctx, history)

    llm_audit_log.log_llm_request(
        request_id,
        {
            "request_id": request_id,
            "user_id": user.get("id"),
            "session_id": session_id,
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

    envelope, raw_text, usage = agent.run_agent(
        req.question,
        ctx,
        history,
        system_prompt=system_prompt,
        user_content=assembled_user_message,
    )
    response_timezone = resolve_query_timezone(ctx.allowed_entities, state, envelope)
    enrich_chart_units(envelope, state, timezone=response_timezone)

    latency_ms = int((time.monotonic() - t0) * 1000)

    llm_audit_log.log_llm_response(
        request_id,
        {
            "raw_model_text": raw_text,
            "parsed_envelope": envelope.to_dict(),
            "validation_errors": usage.get("validation_errors", []),
            "execution_summary": None,
            "token_usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "model_id": usage.get("model_id"),
            },
            "latency_ms": latency_ms,
        },
    )
    audit_path = llm_audit_log.write_audit_bundle(request_id)

    conversation_state.update_from_envelope(session_id, envelope)
    sessions.add_turn(session_id, "user", req.question)
    if envelope.answer:
        sessions.add_turn(session_id, "assistant", envelope.answer)

    chart_details_response = None
    if envelope.chart_details:
        chart_details_response = ChartDetails(**envelope.chart_details.to_dict())

    response_time = _utc_now_iso()
    llm_usage = LlmUsage(
        model_id=usage.get("model_id") or "",
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )
    response = QueryResponse(
        request_id=request_id,
        session_id=session_id,
        request_time=request_time,
        response_time=response_time,
        clarity_required=envelope.clarity_required,
        clarifying_question=envelope.clarifying_question,
        question=envelope.question,
        original_question=req.question,
        answer_type=envelope.answer_type,
        assumption=envelope.assumption,
        suggestions=envelope.suggestions,
        answer=envelope.answer,
        result_template=envelope.result_template,
        chart_applicable=envelope.chart_applicable,
        chart_details=chart_details_response,
        timezone=response_timezone,
        data=None,
        result_summary=None,
        context_warnings=context_warnings,
        llm_usage=llm_usage,
    )

    if user_id:
        history_payload = app_db.history_payload_from_response(response.model_dump())
        app_db.save_query_history(user_id, history_payload)
    if audit_path:
        app_db.log_llm_audit_index(request_id, audit_path)

    return response


@router.post("/query/execute", response_model=ExecuteQueryResponse)
def execute_query(req: ExecuteQueryRequest, user: dict = Depends(get_current_user)):
    """Execute generated SQL after the user clicks Execute in the chat UI."""
    request_id = new_request_id()
    request_time = _utc_now_iso()
    t0 = time.monotonic()

    sql = (req.sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")

    acl = auth.get_acl_for_user(user)
    context_warnings: list[str] = []
    result_summary = None
    response_answer = None

    try:
        raw_result, execution_detail = executor.execute_with_detail(sql, request_id, acl)
    except ValueError as e:
        logger.info("Query execute rejected (%s): %s", request_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.warning("Query execute failed (%s): %s", request_id, e)
        raise HTTPException(status_code=500, detail=f"Execution failed: {e}") from e

    query_data = QueryData(**raw_result)
    execution_summary = {
        "row_count": raw_result.get("row_count"),
        "backend": raw_result.get("backend"),
        "query_time_ms": raw_result.get("query_time_ms"),
        "truncated": raw_result.get("truncated"),
    }
    if execution_detail:
        execution_summary.update(execution_detail)

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

    llm_audit_log.log_llm_response(
        request_id,
        {
            "raw_model_text": None,
            "parsed_envelope": None,
            "validation_errors": [],
            "execution_summary": execution_summary,
            "token_usage": None,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        },
    )
    audit_path = llm_audit_log.write_audit_bundle(request_id)
    if audit_path:
        app_db.log_llm_audit_index(request_id, audit_path)

    return ExecuteQueryResponse(
        request_id=request_id,
        request_time=request_time,
        response_time=_utc_now_iso(),
        data=query_data,
        result_summary=result_summary,
        answer=response_answer,
        context_warnings=context_warnings,
    )


@router.post("/sql", response_model=SqlResponse)
def run_sql(req: SqlRequest, user: dict = Depends(get_current_user)):
    """Execute a SQL statement directly — no LLM, same guards and ACL as /api/query."""
    request_id = new_request_id()
    request_time = _utc_now_iso()
    t0 = time.monotonic()

    sql = (req.sql or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")

    acl = auth.get_acl_for_user(user)
    try:
        raw_result, execution_detail = executor.execute_with_detail(sql, request_id, acl)
    except ValueError as e:
        # Guard/ACL rejection or malformed SQL — client error, not a server fault.
        logger.info("Direct SQL rejected (%s): %s", request_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.warning("Direct SQL execution failed (%s): %s", request_id, e)
        raise HTTPException(status_code=500, detail=f"Execution failed: {e}") from e

    return SqlResponse(
        request_id=request_id,
        request_time=request_time,
        response_time=_utc_now_iso(),
        sql=sql,
        plan=(execution_detail or {}).get("plan"),
        execution_detail=execution_detail,
        latency_ms=int((time.monotonic() - t0) * 1000),
        data=QueryData(**raw_result),
    )


@router.get("/history", response_model=HistorySessionListResponse)
def list_history(
    user: dict = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
):
    user_id = user.get("id") or 0
    if not user_id:
        return HistorySessionListResponse(items=[])
    items = app_db.list_conversation_sessions(user_id, limit=limit)
    return HistorySessionListResponse(items=[HistorySessionItem(**item) for item in items])


@router.get("/history/thread", response_model=HistoryThreadResponse)
def history_thread(
    session_id: str = Query(...),
    user: dict = Depends(get_current_user),
):
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    turns = app_db.get_session_thread(user_id, session_id)
    title = app_db.get_session_display_title(user_id, session_id)
    return HistoryThreadResponse(session_id=session_id, title=title, turns=turns)


@router.post("/history/hydrate", response_model=HistoryThreadResponse)
def hydrate_history_into_session(
    req: HistoryHydrateRequest,
    user: dict = Depends(get_current_user),
):
    """Resume a stored thread: reload turns into memory; keep persisted conversation_state."""
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session_id = req.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    turns = app_db.get_session_thread(user_id, session_id)
    title = app_db.get_session_display_title(user_id, session_id)
    if not turns and not app_db.get_conversation_session_item(user_id, session_id):
        raise HTTPException(status_code=404, detail="Session not found")

    sessions.clear(session_id)
    for turn in turns:
        question = app_db.display_question(turn)
        if question:
            sessions.add_turn(session_id, "user", question)
        answer = turn.get("answer")
        if answer:
            sessions.add_turn(session_id, "assistant", answer)

    return HistoryThreadResponse(
        session_id=session_id,
        title=title,
        turns=turns,
    )


@router.patch("/history/sessions/{session_id}", response_model=HistorySessionItem)
def update_session_title(
    session_id: str,
    req: HistorySessionTitleUpdate,
    user: dict = Depends(get_current_user),
):
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    updated = app_db.update_session_title(user_id, session_id, req.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found or title invalid")
    return HistorySessionItem(**updated)


@router.delete("/history/sessions/{session_id}")
def delete_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    user_id = user.get("id") or 0
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    deleted = app_db.delete_conversation_session(user_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "session_id": session_id}


@router.post("/query/clear")
def clear_session(req: ClearRequest, user: dict = Depends(get_current_user)):
    sessions.clear(req.session_id)
    conversation_state.clear(req.session_id)
    return {"ok": True, "session_id": req.session_id}
