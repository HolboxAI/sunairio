"""Query endpoint — NL to LLM envelope with optional SQL execution."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app import auth, sessions
from app.api.schemas import (
    ChartDetails,
    ClearRequest,
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
from core.session_context import build_session_context, to_prompt_json
from data import app_db
from llm.prompt_loader import load_system_prompt
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

    acl = auth.get_acl_for_user(user)
    state = conversation_state.load(session_id)
    state = conversation_state.merge_user_message_slots(req.question, state)
    ctx = build_session_context(user, acl, state)
    ctx_dict = to_prompt_json(ctx)
    context_warnings = prompt_diff.check_context_against_spec(ctx_dict)
    logger.info(prompt_diff.summarize_for_console(request_id, ctx_dict))

    history = sessions.get_history(session_id)
    system_prompt = load_system_prompt()

    llm_audit_log.log_llm_request(
        request_id,
        {
            "request_id": request_id,
            "user_id": user.get("id"),
            "session_id": session_id,
            "model_id": None,
            "system_prompt_hash": llm_audit_log.prompt_hash(system_prompt),
            "session_context": ctx_dict,
            "user_message": req.question,
            "chat_history": history,
            "context_warnings": context_warnings,
        },
    )

    envelope, raw_text, usage = agent.run_agent(req.question, ctx, history)
    response_timezone = resolve_query_timezone(ctx.allowed_entities, state, envelope)
    enrich_chart_units(envelope, state, timezone=response_timezone)

    query_data = None
    execution_summary = None
    if executor.should_execute(envelope):
        try:
            raw_result, execution_detail = executor.execute_with_detail(
                envelope.answer or "", request_id, acl
            )
            query_data = QueryData(**raw_result)
            execution_summary = {
                "row_count": raw_result.get("row_count"),
                "backend": raw_result.get("backend"),
                "query_time_ms": raw_result.get("query_time_ms"),
                "truncated": raw_result.get("truncated"),
            }
            if execution_detail:
                execution_summary.update(execution_detail)
        except Exception as e:
            logger.warning("SQL execution failed: %s", e)
            context_warnings.append(f"execution_error: {e}")

    latency_ms = int((time.monotonic() - t0) * 1000)

    llm_audit_log.log_llm_response(
        request_id,
        {
            "raw_model_text": raw_text,
            "parsed_envelope": envelope.to_dict(),
            "validation_errors": usage.get("validation_errors", []),
            "execution_summary": execution_summary,
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
        answer_type=envelope.answer_type,
        assumption=envelope.assumption,
        answer=envelope.answer,
        chart_applicable=envelope.chart_applicable,
        chart_details=chart_details_response,
        timezone=response_timezone,
        data=query_data,
        context_warnings=context_warnings,
        llm_usage=llm_usage,
    )

    user_id = user.get("id") or 0
    if user_id:
        history_payload = app_db.history_payload_from_response(response.model_dump())
        app_db.save_query_history(user_id, history_payload)
    if audit_path:
        app_db.log_llm_audit_index(request_id, audit_path)

    return response


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
        question = turn.get("question") or ""
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
