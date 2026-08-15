"""Analytics v2 API — LLM1 consult + resolver confirmation (Phase 1)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from analytics import historical_scalar, metadata_answer, session_store
from analytics.ambiguity import (
    apply_resolved_slots,
    check_ambiguity,
    detect_clarification_resolution,
    slot_to_ref,
    slots_from_refs,
)
from analytics.chart_infer import infer_chart_from_rep
from analytics.catalog import build_llm1_injection
from analytics.intent import is_awareness, is_metadata
from analytics.llm1 import agent as llm1_agent
from analytics.llm2 import run as llm2_run
from analytics.resolver.pipeline import resolve_aep
from analytics.resolver.voice import compose_clarify_message, prefer_human_confirm_message
from analytics.result_refs import (
    apply_session_thresholds,
    extract_from_result,
    infer_period_from_timeframe,
)
from analytics.session_context import (
    build_session_context_block,
    infer_resolved_slots,
    load_session_refs,
)
from app import auth
from app.api.schemas import (
    AnalyticsConfirmRequest,
    AnalyticsConfirmResponse,
    AnalyticsConsultRequest,
    AnalyticsConsultResponse,
    AnalyticsHistoryHydrateRequest,
    AnalyticsHistoryThreadResponse,
    AnalyticsLlmUsage,
    ChartDetails,
    HistorySessionItem,
    HistorySessionListResponse,
    HistorySessionTitleUpdate,
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

    clarify_slots = detect_clarification_resolution(message)
    for slot_key, slot_val in clarify_slots.items():
        try:
            session_store.save_reference(session_id, slot_to_ref(slot_key, slot_val))
        except Exception as e:
            logger.warning("Failed to persist clarification slot %s: %s", slot_key, e)

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
    refs = load_session_refs(session_id, user_id) or []
    session_slots = infer_resolved_slots(history)
    session_slots.update(slots_from_refs(refs))
    session_context = build_session_context_block(
        refs=refs,
        history=history,
        resolved_slots=session_slots,
    )

    try:
        aep, raw_text, usage = llm1_agent.run_llm1(
            message,
            injection,
            history[:-1],
            session_context=session_context or None,
        )
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
                link_conversation=False,
            )
        except Exception as e:
            logger.warning("Failed to persist analytics usage history: %s", e)

    llm_usage = AnalyticsLlmUsage(
        model_id=usage.get("model_id") or "",
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )

    # If the user is asking what a symbolic pending threshold means, fetch it from
    # Metadata actuals — do not let an awareness reply invent a MW figure.
    pending = session_store.get_pending_rep_for_session(session_id, user_id)
    threshold_followup = historical_scalar.try_answer_threshold_followup(
        message, pending
    )
    if threshold_followup is not None:
        answer_text, scalar_result, hist_rep = threshold_followup
        try:
            session_store.save_reference(
                session_id, historical_scalar.result_to_ref(scalar_result)
            )
        except Exception as e:
            logger.warning("Failed to persist threshold follow-up reference: %s", e)
        session_store.add_turn(
            session_id,
            "assistant",
            answer_text,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="answered",
                assistant_message=answer_text,
                rep_preview=hist_rep.to_dict(),
                notes=list(hist_rep.notes),
                llm_usage=llm_usage,
            ),
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
        patched = apply_resolved_slots(aep, session_slots)
        patched = apply_session_thresholds(patched, refs, message)
        if clarify_slots and not check_ambiguity(
            message,
            patched,
            refs=refs,
            session_slots=session_slots,
        ):
            aep = patched
            aep.status = "resolved"
        else:
            questions = list(aep.clarification_questions)
            assistant_message = aep.assistant_message or (
                "\n".join(questions) if questions else "I need a bit more detail to finalize the analysis."
            )
            # When LLM1 already wrote the user-facing ask, do not also return the
            # questions list — the UI appends non-exact matches and the reply reads
            # as a duplicated questionnaire.
            ui_questions: list[str] = []
            if not (aep.assistant_message or "").strip():
                ui_questions = questions
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
                    questions=ui_questions,
                    notes=list(aep.notes),
                    llm_usage=llm_usage,
                ),
            )

    aep = apply_resolved_slots(aep, session_slots)
    aep = apply_session_thresholds(aep, refs, message)

    ambiguity_msg = check_ambiguity(
        message,
        aep,
        refs=refs,
        session_slots=session_slots,
    )
    if ambiguity_msg:
        session_store.add_turn(
            session_id,
            "assistant",
            ambiguity_msg,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="clarify",
                assistant_message=ambiguity_msg,
                questions=[],
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
        entity_variables=resolver_payload.get("entity_variables") or {},
        current_utc=injection.get("current_utc") or _utc_now_iso(),
        user_message=message,
        session_slots=session_slots,
        session_refs=refs,
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

    # A catalog lookup is already answerable from the injected catalog. Asking the
    # user to confirm "ERCOT → Available locations" adds a round trip and still
    # shows them no locations, so answer it here instead.
    if is_metadata(aep.query.intent):
        prior_locs = next(
            (r for r in refs if r.get("kind") == "catalog_location_list"),
            None,
        )
        catalog_answer, loc_ref = metadata_answer.answer(
            aep,
            rep,
            message=message,
            allowed_entities=resolver_payload.get("allowed_entities") or [],
            entity_catalog=resolver_payload.get("entity_catalog") or {},
            entity_variables=resolver_payload.get("entity_variables") or {},
            variable_catalog=resolver_payload.get("variable_catalog") or [],
            latest_inits=resolver_payload.get("latest_inits") or {},
            location_types=injection.get("location_types") or {},
            catalog_locations=prior_locs,
        )
        if catalog_answer:
            if loc_ref:
                try:
                    session_store.save_reference(session_id, loc_ref)
                except Exception as e:
                    logger.warning("Failed to persist catalog location list: %s", e)
            session_store.add_turn(
                session_id,
                "assistant",
                catalog_answer,
                aep=aep.to_dict(),
            )
            return _finalize(
                request_id,
                AnalyticsConsultResponse(
                    request_id=request_id,
                    session_id=session_id,
                    phase="answered",
                    assistant_message=catalog_answer,
                    notes=list(aep.notes),
                    llm_usage=llm_usage,
                ),
            )

    # Fully-bound historical scalar (e.g. 2023 max load) can be fetched from
    # Metadata DB actuals immediately. On any miss/error, fall through to confirm.
    scalar = historical_scalar.try_answer(rep)
    if scalar is not None:
        answer_text, scalar_result = scalar
        try:
            session_store.save_reference(
                session_id, historical_scalar.result_to_ref(scalar_result)
            )
        except Exception as e:
            logger.warning("Failed to persist historical scalar reference: %s", e)
        session_store.add_turn(
            session_id,
            "assistant",
            answer_text,
            aep=aep.to_dict(),
        )
        return _finalize(
            request_id,
            AnalyticsConsultResponse(
                request_id=request_id,
                session_id=session_id,
                phase="answered",
                assistant_message=answer_text,
                summary=summary.to_dict(),
                rep_preview=rep.to_dict(),
                notes=list(aep.notes),
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
        consult_request_id=request_id,
    )
    assistant_message = prefer_human_confirm_message(
        aep.assistant_message,
        summary,
        rep,
        user_message=message,
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
    rep = stored.get("rep") or {}
    outcome = llm2_run.run_confirmed_plan(rep, request_id=request_id)
    msg = outcome.get("message") or "Query finished."
    result_payload = dict(outcome.get("data") or {})
    if outcome.get("sql"):
        result_payload["sql"] = outcome.get("sql")
    if outcome.get("target"):
        result_payload["target"] = outcome.get("target")

    chart_applicable, chart_details_raw, entity_tz = infer_chart_from_rep(
        rep, outcome.get("data")
    )
    chart_details_response = None
    if chart_applicable and chart_details_raw:
        chart_details_response = ChartDetails(**chart_details_raw)
        result_payload["chart_applicable"] = True
        result_payload["chart_details"] = chart_details_raw
        if entity_tz:
            result_payload["timezone"] = entity_tz

    session_store.add_turn(
        req.session_id,
        "assistant",
        msg,
        result_data=result_payload if (outcome.get("ok") or outcome.get("sql")) else None,
    )

    if outcome.get("ok") and result_payload.get("rows"):
        rep = stored.get("rep") or {}
        summary = stored.get("summary") or {}
        entity = (rep.get("entity") or {}).get("name") or summary.get("entity") or ""
        tf = rep.get("timeframe") or {}
        period = infer_period_from_timeframe(tf.get("start"), tf.get("end"))
        table_ref = extract_from_result(
            result_payload,
            entity=str(entity),
            period=period,
        )
        if table_ref:
            try:
                session_store.save_reference(req.session_id, table_ref)
            except Exception as e:
                logger.warning("Failed to persist location threshold table: %s", e)

    usage_raw = outcome.get("llm_usage") or {}
    llm_usage = AnalyticsLlmUsage(
        model_id=usage_raw.get("model_id") or "",
        input_tokens=int(usage_raw.get("input_tokens") or 0),
        output_tokens=int(usage_raw.get("output_tokens") or 0),
    )
    phase = "answered" if outcome.get("ok") else "error"
    plan = outcome.get("plan") or {}
    confirm_response = AnalyticsConfirmResponse(
        request_id=request_id,
        session_id=req.session_id,
        phase=phase,
        rep_id=req.rep_id,
        message=msg,
        summary=stored.get("summary"),
        sql=outcome.get("sql"),
        target=outcome.get("target"),
        data=outcome.get("data"),
        result_summary=outcome.get("result_summary"),
        errors=list(outcome.get("errors") or []),
        llm_usage=llm_usage,
        execution=outcome.get("execution"),
        chart_applicable=chart_applicable,
        chart_details=chart_details_response,
        timezone=entity_tz,
        assumptions=[str(a) for a in (plan.get("assumptions") or []) if str(a).strip()],
    )
    llm2_debug = outcome.get("llm2_debug") or {}
    consult_log.append_confirm_log(
        stored.get("consult_request_id") or "",
        confirm_request_id=request_id,
        payload={
            "llm2_request": {
                "assembled_user_message": llm2_debug.get("assembled_user_message"),
            },
            "llm2_response": {
                "raw_model_text": outcome.get("raw_text"),
                "parsed_plan": outcome.get("plan"),
                "validation_errors": llm2_debug.get("validation_errors") or [],
                "latency_ms": llm2_debug.get("latency_ms"),
                "input_tokens": llm2_debug.get("input_tokens", 0),
                "output_tokens": llm2_debug.get("output_tokens", 0),
            },
            "executor": {
                "sql": outcome.get("sql"),
                "detail": outcome.get("execution"),
                "result_summary": {
                    "row_count": (outcome.get("data") or {}).get("row_count"),
                    "backend": outcome.get("target"),
                },
            },
            "confirm_response": confirm_response.model_dump(),
        },
    )
    return confirm_response


@router.get("/history", response_model=HistorySessionListResponse)
def list_analytics_history(
    user: dict = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
):
    session_store.ensure_tables()
    user_id = int(user.get("id") or 0)
    if not user_id:
        return HistorySessionListResponse(items=[])
    items = session_store.list_sessions(user_id, limit=limit)
    return HistorySessionListResponse(items=[HistorySessionItem(**item) for item in items])


@router.post("/history/hydrate", response_model=AnalyticsHistoryThreadResponse)
def hydrate_analytics_history(
    req: AnalyticsHistoryHydrateRequest,
    user: dict = Depends(get_current_user),
):
    session_store.ensure_tables()
    user_id = int(user.get("id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session_id = (req.session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    turns = session_store.get_thread(session_id, user_id)
    if turns is None:
        raise HTTPException(status_code=404, detail="Session not found")

    item = session_store.get_session_item(user_id, session_id)
    title = (item or {}).get("title") or "Untitled conversation"
    pending = session_store.get_pending_rep_for_session(session_id, user_id)
    pending_payload = None
    if pending:
        pending_payload = {
            "rep_id": pending["rep_id"],
            "summary": pending.get("summary"),
            "rep_preview": pending.get("rep"),
        }

    return AnalyticsHistoryThreadResponse(
        session_id=session_id,
        title=title,
        turns=turns,
        pending_rep=pending_payload,
    )


@router.patch("/history/sessions/{session_id}", response_model=HistorySessionItem)
def update_analytics_session_title(
    session_id: str,
    req: HistorySessionTitleUpdate,
    user: dict = Depends(get_current_user),
):
    session_store.ensure_tables()
    user_id = int(user.get("id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    updated = session_store.update_session_title(user_id, session_id, req.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found or title invalid")
    return HistorySessionItem(**updated)


@router.delete("/history/sessions/{session_id}")
def delete_analytics_session(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    session_store.ensure_tables()
    user_id = int(user.get("id") or 0)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    deleted = session_store.delete_session(user_id, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "session_id": session_id}
