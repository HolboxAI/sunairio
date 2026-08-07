"""Analytics v2 API — LLM1 consult + resolver confirmation (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from analytics import session_store
from analytics.catalog import build_llm1_injection
from analytics.intent import is_awareness, is_metadata
from analytics.llm1 import agent as llm1_agent
from analytics.resolver.pipeline import resolve_aep
from analytics.resolver.voice import compose_clarify_message, prefer_human_confirm_message
from app import auth
from app.api.schemas import (
    AnalyticsConfirmRequest,
    AnalyticsConfirmResponse,
    AnalyticsConsultRequest,
    AnalyticsConsultResponse,
    AnalyticsLlmUsage,
)
from app.deps import get_current_user, new_request_id
from data import app_db
from observability import analytics_consult_log as consult_log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["analytics"])


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


def _finalize(request_id: str, response: AnalyticsConsultResponse) -> AnalyticsConsultResponse:
    """Record what the user is about to see, then flush this turn's log file."""
    consult_log.log_user_response(
        request_id,
        {"phase": response.phase, "body": response.model_dump()},
    )
    consult_log.write_consult_log(request_id)
    return response


@router.post("/consult", response_model=AnalyticsConsultResponse)
def consult(req: AnalyticsConsultRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_id = req.session_id or request_id
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    _check_token_limit(user)
    session_store.ensure_tables()
    user_id = int(user.get("id") or 0)
    if not session_store.touch_session(session_id, user_id):
        raise HTTPException(status_code=403, detail="Session belongs to another user")
    session_store.add_turn(session_id, "user", message)

    consult_log.start(
        request_id,
        {
            "session_id": session_id,
            "user": f"{user.get('email') or ''} (id={user_id})",
            "user_message": message,
        },
    )

    acl = auth.get_acl_for_user(user)
    injection = build_llm1_injection(user, acl)
    resolver_payload = injection.get("_resolver") or {}
    history = session_store.get_history(session_id)

    try:
        aep, raw_text, usage = llm1_agent.run_llm1(message, injection, history[:-1])
    except Exception as e:
        logger.exception("LLM1 consult failed (%s)", request_id)
        consult_log.log_llm1_response(request_id, {"error": str(e)})
        consult_log.write_consult_log(request_id)
        raise HTTPException(status_code=502, detail=f"Consultant failed: {e}") from e

    system_prompt = usage.get("system_prompt") or ""
    consult_log.log_llm1_request(
        request_id,
        {
            "model_id": usage.get("model_id"),
            "system_prompt": system_prompt,
            "system_prompt_hash": consult_log.prompt_hash(system_prompt),
            "assembled_user_message": usage.get("assembled_user_message") or "",
            "history_turns": usage.get("history_turns", 0),
        },
    )
    consult_log.log_llm1_response(
        request_id,
        {
            "raw_model_text": raw_text,
            "parsed_aep": aep.to_dict(),
            "validation_errors": usage.get("validation_errors") or [],
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "model_id": usage.get("model_id"),
            "latency_ms": usage.get("latency_ms"),
        },
    )

    # Record token usage on the anonymous/history path used by v1 when possible
    if user_id and usage:
        try:
            app_db.save_query_history(
                user_id,
                {
                    "request_id": request_id,
                    "session_id": session_id,
                    "request_time": _utc_now_iso(),
                    "response_time": _utc_now_iso(),
                    "clarity_required": aep.status != "resolved",
                    "clarifying_question": aep.clarification_questions,
                    "question": message,
                    "original_question": message,
                    # v1 vocabulary is Sql | Metadata | Awareness; the consult
                    # stage never emits SQL, so it is only ever the latter two.
                    "answer_type": "Metadata" if is_metadata(aep.query.intent) else "Awareness",
                    "assumption": [],
                    "answer": aep.assistant_message,
                    "chart_applicable": False,
                    "chart_details": None,
                    "timezone": None,
                    "result_summary": None,
                    "context_warnings": usage.get("validation_errors") or [],
                    "llm_usage": {
                        "model_id": usage.get("model_id") or "",
                        "input_tokens": int(usage.get("input_tokens") or 0),
                        "output_tokens": int(usage.get("output_tokens") or 0),
                    },
                },
            )
        except Exception as e:
            logger.warning("Failed to persist analytics usage history: %s", e)

    llm_usage = AnalyticsLlmUsage(
        model_id=usage.get("model_id") or "",
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )

    # Awareness / capability chat is answered in-conversation — never run the
    # forecast-style resolver (that produced mechanical "Entity is required").
    if is_awareness(aep.query.intent):
        assistant_message = aep.assistant_message or (
            "\n".join(aep.clarification_questions)
            if aep.clarification_questions
            else "Happy to help — what would you like to analyze?"
        )
        session_store.add_turn(
            session_id,
            "assistant",
            assistant_message,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="answered",
                assistant_message=assistant_message,
                questions=list(aep.clarification_questions),
                notes=list(aep.notes),
                llm_usage=llm_usage,
            ),
        )

    if aep.status != "resolved":
        questions = list(aep.clarification_questions)
        assistant_message = aep.assistant_message or (
            "\n".join(questions) if questions else "I need a bit more detail to finalize the analysis."
        )
        session_store.add_turn(
            session_id,
            "assistant",
            assistant_message,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="clarify",
                assistant_message=assistant_message,
                questions=questions,
                notes=list(aep.notes),
                llm_usage=llm_usage,
            ),
        )

    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=resolver_payload.get("allowed_entities") or [],
        latest_inits=resolver_payload.get("latest_inits") or {},
        entity_catalog=resolver_payload.get("entity_catalog") or {},
        variable_catalog=resolver_payload.get("variable_catalog") or [],
        current_utc=injection.get("current_utc") or _utc_now_iso(),
    )
    consult_log.log_resolver(
        request_id,
        {
            "errors": list(errors),
            "rep": rep.to_dict() if rep else None,
            "summary": summary.to_dict() if summary else None,
        },
    )

    if errors or not rep or not summary:
        questions = list(errors) if errors else []
        assistant_message = compose_clarify_message(
            questions,
            prior_message=aep.assistant_message,
        )
        session_store.add_turn(
            session_id,
            "assistant",
            assistant_message,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="clarify",
                assistant_message=assistant_message,
                # Questions already woven into assistant_message — avoid JSON-list UX
                questions=[],
                notes=list(aep.notes),
                errors=errors,
                llm_usage=llm_usage,
            ),
        )

    summary_dict = summary.to_dict()
    rep_dict = rep.to_dict()
    rep_id = session_store.save_pending_rep(
        session_id,
        aep.to_dict(),
        rep_dict,
        summary_dict,
    )
    assistant_message = prefer_human_confirm_message(
        aep.assistant_message,
        summary,
        rep,
    )
    session_store.add_turn(
        session_id,
        "assistant",
        assistant_message,
        aep=aep.to_dict(),
    )
    return _finalize(
        request_id,
        AnalyticsConsultResponse(
            request_id=request_id,
            session_id=session_id,
            phase="confirm",
            assistant_message=assistant_message,
            summary=summary_dict,
            rep_id=rep_id,
            rep_preview=rep_dict,
            notes=list(aep.notes),
            llm_usage=llm_usage,
        ),
    )


@router.post("/confirm", response_model=AnalyticsConfirmResponse)
def confirm(req: AnalyticsConfirmRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_store.ensure_tables()

    action = (req.action or "").strip().lower()
    if action not in ("confirm", "reject"):
        raise HTTPException(status_code=400, detail="action must be confirm or reject")

    user_id = int(user.get("id") or 0)
    stored = session_store.get_rep(req.rep_id, user_id=user_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Resolved plan not found")
    if stored["session_id"] != req.session_id:
        raise HTTPException(status_code=400, detail="rep_id does not match session_id")
    if stored["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Plan is already {stored['status']}",
        )

    if not session_store.touch_session(req.session_id, user_id):
        raise HTTPException(status_code=403, detail="Session belongs to another user")

    if action == "reject":
        session_store.set_rep_status(req.rep_id, "rejected")
        msg = "Plan discarded. Tell me what to change and we can refine it."
        session_store.add_turn(req.session_id, "assistant", msg)
        return AnalyticsConfirmResponse(
            request_id=request_id,
            session_id=req.session_id,
            phase="clarify",
            rep_id=req.rep_id,
            message=msg,
            summary=stored.get("summary"),
        )

    session_store.set_rep_status(req.rep_id, "confirmed")
    msg = (
        "Resolved execution plan confirmed and locked. "
        "SQL generation (LLM2) will be available in Phase 2."
    )
    session_store.add_turn(req.session_id, "assistant", msg)
    return AnalyticsConfirmResponse(
        request_id=request_id,
        session_id=req.session_id,
        phase="confirmed",
        rep_id=req.rep_id,
        message=msg,
        summary=stored.get("summary"),
    )
