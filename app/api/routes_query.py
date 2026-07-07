"""Query endpoint — NL to LLM envelope (v1, no SQL execution)."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends

from app import auth, sessions
from app.api.schemas import ChartDetails, ClearRequest, QueryRequest, QueryResponse
from app.deps import get_current_user, new_request_id
from core import agent, conversation_state
from core.chart_units import enrich_chart_units
from core.session_context import build_session_context, to_prompt_json
from data import app_db
from llm.prompt_loader import load_system_prompt
from observability import llm_audit_log, prompt_diff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_id = req.session_id or request_id
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
    enrich_chart_units(envelope, state)
    latency_ms = int((time.monotonic() - t0) * 1000)

    llm_audit_log.log_llm_response(
        request_id,
        {
            "raw_model_text": raw_text,
            "parsed_envelope": envelope.to_dict(),
            "validation_errors": usage.get("validation_errors", []),
            "token_usage": {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "model_id": usage.get("model_id"),
            },
            "latency_ms": latency_ms,
        },
    )
    audit_path = llm_audit_log.write_audit_bundle(request_id)

    user_id = user.get("id") or 0
    if user_id:
        app_db.log_query_envelope(user_id, request_id, req.question, envelope.to_dict(), session_id)
    if audit_path:
        app_db.log_llm_audit_index(request_id, audit_path)

    conversation_state.update_from_envelope(session_id, envelope)
    sessions.add_turn(session_id, "user", req.question)
    if envelope.answer:
        sessions.add_turn(session_id, "assistant", envelope.answer)

    chart_details_response = None
    if envelope.chart_details:
        chart_details_response = ChartDetails(**envelope.chart_details.to_dict())

    return QueryResponse(
        request_id=request_id,
        session_id=session_id,
        clarity_required=envelope.clarity_required,
        clarifying_question=envelope.clarifying_question,
        question=envelope.question,
        answer_type=envelope.answer_type,
        assumption=envelope.assumption,
        answer=envelope.answer,
        chart_applicable=envelope.chart_applicable,
        chart_details=chart_details_response,
        context_warnings=context_warnings,
    )


@router.post("/query/clear")
def clear_session(req: ClearRequest, user: dict = Depends(get_current_user)):
    sessions.clear(req.session_id)
    conversation_state.clear(req.session_id)
    return {"ok": True, "session_id": req.session_id}
