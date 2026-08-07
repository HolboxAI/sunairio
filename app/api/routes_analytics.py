"""Analytics v2 API — LLM1 consult + resolver confirmation (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from analytics import session_store
from analytics.catalog import build_llm1_injection
from analytics.llm1 import agent as llm1_agent
from analytics.resolver.pipeline import resolve_aep
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
    session_store.touch_session(session_id, user_id)
    session_store.add_turn(session_id, "user", message)

    acl = auth.get_acl_for_user(user)
    injection = build_llm1_injection(user, acl)
    resolver_payload = injection.get("_resolver") or {}
    history = session_store.get_history(session_id)

    try:
        aep, _raw, usage = llm1_agent.run_llm1(message, injection, history[:-1])
    except Exception as e:
        logger.exception("LLM1 consult failed (%s)", request_id)
        raise HTTPException(status_code=502, detail=f"Consultant failed: {e}") from e

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
                    "answer_type": "Awareness",
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
        return AnalyticsConsultResponse(
            request_id=request_id,
            session_id=session_id,
            phase="clarify",
            assistant_message=assistant_message,
            questions=questions,
            notes=list(aep.notes),
            llm_usage=llm_usage,
        )

    rep, summary, errors = resolve_aep(
        aep,
        allowed_entities=resolver_payload.get("allowed_entities") or [],
        latest_inits=resolver_payload.get("latest_inits") or {},
        entity_catalog=resolver_payload.get("entity_catalog") or {},
        variable_catalog=resolver_payload.get("variable_catalog") or [],
        current_utc=injection.get("current_utc") or _utc_now_iso(),
    )

    if errors or not rep or not summary:
        err_msg = "; ".join(errors) if errors else "Could not resolve the analytical plan."
        assistant_message = (
            aep.assistant_message
            or "I understood your intent, but need clarification before confirmation."
        )
        assistant_message = f"{assistant_message}\n\n{err_msg}"
        session_store.add_turn(
            session_id,
            "assistant",
            assistant_message,
            aep=aep.to_dict(),
        )
        return AnalyticsConsultResponse(
            request_id=request_id,
            session_id=session_id,
            phase="clarify",
            assistant_message=assistant_message,
            questions=errors,
            notes=list(aep.notes),
            errors=errors,
            llm_usage=llm_usage,
        )

    summary_dict = summary.to_dict()
    rep_dict = rep.to_dict()
    rep_id = session_store.save_pending_rep(
        session_id,
        aep.to_dict(),
        rep_dict,
        summary_dict,
    )
    assistant_message = (
        aep.assistant_message
        or "Here is the resolved plan. Please confirm to proceed."
    )
    session_store.add_turn(
        session_id,
        "assistant",
        assistant_message,
        aep=aep.to_dict(),
    )
    return AnalyticsConsultResponse(
        request_id=request_id,
        session_id=session_id,
        phase="confirm",
        assistant_message=assistant_message,
        summary=summary_dict,
        rep_id=rep_id,
        rep_preview=rep_dict,
        notes=list(aep.notes),
        llm_usage=llm_usage,
    )


@router.post("/confirm", response_model=AnalyticsConfirmResponse)
def confirm(req: AnalyticsConfirmRequest, user: dict = Depends(get_current_user)):
    request_id = new_request_id()
    session_store.ensure_tables()

    action = (req.action or "").strip().lower()
    if action not in ("confirm", "reject"):
        raise HTTPException(status_code=400, detail="action must be confirm or reject")

    stored = session_store.get_rep(req.rep_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Resolved plan not found")
    if stored["session_id"] != req.session_id:
        raise HTTPException(status_code=400, detail="rep_id does not match session_id")
    if stored["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Plan is already {stored['status']}",
        )

    user_id = int(user.get("id") or 0)
    session_store.touch_session(req.session_id, user_id)

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
