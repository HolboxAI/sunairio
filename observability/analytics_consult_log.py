"""Per-turn audit log for the analytics consult pipeline.

One file per consult turn. Confirm (LLM2 + executor + user reply) appends to
that same file. Every payload is written verbatim — no clipping of prompts,
catalogs, SQL, or result rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_bundles: Dict[str, Dict[str, Any]] = {}

_WIDTH = 80


def _log_dir() -> Path:
    d = Path(settings.analytics_consult_log_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256((system_prompt or "").encode()).hexdigest()[:16]


def start(request_id: str, payload: Dict[str, Any]) -> None:
    """Open a bundle for this turn. Safe to call again for the same request."""
    bundle = _bundles.setdefault(request_id, {})
    bundle["meta"] = {"started_at": _utc_now(), **payload}


def log_llm1_request(request_id: str, payload: Dict[str, Any]) -> None:
    _bundles.setdefault(request_id, {})["llm1_request"] = {
        "timestamp": _utc_now(),
        **payload,
    }


def log_llm1_response(request_id: str, payload: Dict[str, Any]) -> None:
    _bundles.setdefault(request_id, {})["llm1_response"] = {
        "timestamp": _utc_now(),
        **payload,
    }


def log_resolver(request_id: str, payload: Dict[str, Any]) -> None:
    _bundles.setdefault(request_id, {})["resolver"] = {
        "timestamp": _utc_now(),
        **payload,
    }


def log_user_response(request_id: str, payload: Dict[str, Any]) -> None:
    _bundles.setdefault(request_id, {})["user_response"] = {
        "timestamp": _utc_now(),
        **payload,
    }


def get_bundle(request_id: str) -> Optional[Dict[str, Any]]:
    return _bundles.get(request_id)


def _heading(title: str) -> str:
    return f"\n{'=' * _WIDTH}\n{title}\n{'=' * _WIDTH}"


def _section(title: str) -> str:
    return f"\n{'-' * _WIDTH}\n{title}\n{'-' * _WIDTH}"


def _dump(body: Any) -> str:
    """Serialize a log payload with no length cap."""
    if body is None:
        return "(empty)"
    if isinstance(body, str):
        return body if body else "(empty)"
    return json.dumps(body, indent=2, default=str, ensure_ascii=False)


def _block(label: str, body: Any) -> str:
    return f"\n--- {label} ---\n{_dump(body)}\n"


def _render(request_id: str, bundle: Dict[str, Any]) -> str:
    meta = bundle.get("meta") or {}
    req = bundle.get("llm1_request") or {}
    res = bundle.get("llm1_response") or {}
    resolver = bundle.get("resolver") or {}
    user_res = bundle.get("user_response") or {}
    resolver_in = resolver.get("input") if isinstance(resolver.get("input"), dict) else {}
    ran_resolver = bool(resolver)

    out = [
        _heading("ANALYTICS CONSULT TURN"),
        f"request_id  : {request_id}",
        f"session_id  : {meta.get('session_id', '')}",
        f"user        : {meta.get('user', '')}",
        f"started_at  : {meta.get('started_at', '')}",
        _section("1. USER REQUEST"),
        _block("user message", meta.get("user_message")),
        _section("2. LLM1 INPUT REQUEST"),
        f"model             : {req.get('model_id') or '(resolved at call time)'}",
        f"system_prompt_sha : {req.get('system_prompt_hash', '')}",
        f"history_turns     : {req.get('history_turns', 0)}",
        _block("system prompt", req.get("system_prompt")),
        _block("assembled user message", req.get("assembled_user_message")),
        _section("3. LLM1 OUTPUT RESPONSE"),
    ]
    if res.get("error"):
        out.append(f"error      : {res['error']}")
    out.extend(
        [
            f"latency_ms : {res.get('latency_ms', '')}",
            f"tokens     : in={res.get('input_tokens', 0)} out={res.get('output_tokens', 0)}",
            f"model_id   : {res.get('model_id', '')}",
            f"validation : {res.get('validation_errors', [])}",
            _block("raw model text", res.get("raw_model_text")),
            _block("parsed analytical execution plan (AEP)", res.get("parsed_aep")),
            _section("4. RESOLVER INPUT"),
        ]
    )
    if ran_resolver:
        out.extend(
            [
                "ran        : yes",
                _block("analytical execution plan (AEP)", resolver_in.get("aep")),
                _block("user_message", resolver_in.get("user_message")),
                _block("current_utc", resolver_in.get("current_utc")),
                _block("session_slots", resolver_in.get("session_slots")),
                _block("session_refs", resolver_in.get("session_refs")),
                _block("allowed_entities", resolver_in.get("allowed_entities")),
                _block("latest_inits", resolver_in.get("latest_inits")),
                _block("entity_catalog", resolver_in.get("entity_catalog")),
                _block("variable_catalog", resolver_in.get("variable_catalog")),
                _block("entity_variables", resolver_in.get("entity_variables")),
                _section("5. RESOLVER OUTPUT"),
                f"errors     : {resolver.get('errors', [])}",
                _block("resolved execution plan (REP)", resolver.get("rep")),
                _block("confirmation summary", resolver.get("summary")),
            ]
        )
    else:
        out.extend(
            [
                "ran        : no (short-circuited before resolution)",
                _section("5. RESOLVER OUTPUT"),
                "ran        : no (short-circuited before resolution)",
            ]
        )

    out.extend(
        [
            _section("6. RESPONSE TO USER (consult)"),
            f"phase      : {user_res.get('phase', '')}",
            _block("response body", user_res.get("body")),
            "=" * _WIDTH,
            "",
            "(LLM2 sections are appended to this file on confirm.)",
        ]
    )
    return "\n".join(out) + "\n"


def append_confirm_log(
    consult_request_id: str,
    *,
    confirm_request_id: str,
    payload: Dict[str, Any],
) -> Optional[str]:
    """Append LLM2 + executor + confirm reply to the consult log for this turn."""
    rid = (consult_request_id or "").strip()
    if not rid:
        return _write_orphan_confirm(confirm_request_id, payload)
    try:
        matches = sorted(_log_dir().glob(f"*_{rid}.log"))
        if not matches:
            logger.warning("No consult log found to append confirm for %s", rid)
            return _write_orphan_confirm(confirm_request_id, payload)
        path = matches[-1]
        sections = _render_confirm_sections(confirm_request_id, payload)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(sections)
        logger.info("Appended confirm execution log to %s", path)
        return str(path)
    except Exception as e:
        logger.warning(
            "Failed to append confirm log for consult %s: %s", rid, e
        )
        return None


def _write_orphan_confirm(confirm_request_id: str, payload: Dict[str, Any]) -> Optional[str]:
    """If the consult file is missing, still persist LLM2 + user reply untruncated."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
        path = _log_dir() / f"{ts}_{confirm_request_id}_confirm.log"
        path.write_text(
            _heading("ANALYTICS CONFIRM (consult log missing)")
            + "\n"
            + _render_confirm_sections(confirm_request_id, payload),
            encoding="utf-8",
        )
        logger.info("Wrote standalone confirm log: %s", path)
        return str(path)
    except Exception as e:
        logger.warning("Failed to write standalone confirm log: %s", e)
        return None


def _render_confirm_sections(confirm_request_id: str, payload: Dict[str, Any]) -> str:
    llm2_req = payload.get("llm2_request") or {}
    llm2_res = payload.get("llm2_response") or {}
    executor = payload.get("executor") or {}
    confirm_res = payload.get("confirm_response")
    out = [
        _section("7. LLM2 INPUT REQUEST"),
        f"confirm_request_id : {confirm_request_id}",
        f"model              : {llm2_req.get('model_id') or llm2_res.get('model_id') or ''}",
        f"system_prompt_sha  : {prompt_hash(str(llm2_req.get('system_prompt') or ''))}",
        _block("system prompt", llm2_req.get("system_prompt")),
        _block("assembled user message", llm2_req.get("assembled_user_message")),
        _section("8. LLM2 OUTPUT RESPONSE"),
        f"latency_ms         : {llm2_res.get('latency_ms', '')}",
        f"tokens             : in={llm2_res.get('input_tokens', 0)} out={llm2_res.get('output_tokens', 0)}",
        f"validation         : {llm2_res.get('validation_errors', [])}",
        _block("raw model text", llm2_res.get("raw_model_text")),
        _block("parsed SQL plan", llm2_res.get("parsed_plan")),
        _section("9. EXECUTOR"),
        _block("SQL executed", executor.get("sql")),
        _block("execution detail", executor.get("detail")),
        _block("result data", executor.get("data")),
        _block("result summary", executor.get("result_summary")),
        _section("10. RESPONSE TO USER (confirm)"),
        _block("response body", confirm_res),
        "=" * _WIDTH,
    ]
    return "\n".join(out) + "\n"


def write_consult_log(request_id: str) -> Optional[str]:
    """Flush this turn's bundle to disk. Never raises — logging must not break a request."""
    bundle = _bundles.pop(request_id, None)
    if not bundle:
        return None
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
        path = _log_dir() / f"{ts}_{request_id}.log"
        path.write_text(_render(request_id, bundle), encoding="utf-8")
        logger.info("Analytics consult log written: %s", path)
        return str(path)
    except Exception as e:
        logger.warning("Failed to write analytics consult log for %s: %s", request_id, e)
        return None
