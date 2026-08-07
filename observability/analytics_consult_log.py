"""Per-turn audit log for the analytics consult flow (LLM1 → Resolver → user).

One human-readable file per /api/v2/consult turn, holding the three things worth
eyeballing when a plan comes out wrong: the exact prompt sent to LLM1, the raw
reply it sent back, and the payload handed to the user after the resolver ran.

Prompts are written verbatim rather than JSON-escaped so they can be read (and
diffed) directly.
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


def _block(label: str, body: Any) -> str:
    text = body if isinstance(body, str) else json.dumps(body, indent=2, default=str)
    return f"\n--- {label} ---\n{text if text else '(empty)'}\n"


def _render(request_id: str, bundle: Dict[str, Any]) -> str:
    meta = bundle.get("meta") or {}
    req = bundle.get("llm1_request") or {}
    res = bundle.get("llm1_response") or {}
    resolver = bundle.get("resolver") or {}
    user_res = bundle.get("user_response") or {}

    out = [
        _heading("ANALYTICS CONSULT TURN"),
        f"request_id  : {request_id}",
        f"session_id  : {meta.get('session_id', '')}",
        f"user        : {meta.get('user', '')}",
        f"started_at  : {meta.get('started_at', '')}",
        f"user_message: {meta.get('user_message', '')}",
    ]

    out.append(_section("1. FINAL PROMPT SENT TO LLM1"))
    out.append(f"model             : {req.get('model_id') or '(resolved at call time)'}")
    out.append(f"system_prompt_sha : {req.get('system_prompt_hash', '')}")
    out.append(f"history_turns     : {req.get('history_turns', 0)}")
    out.append(_block("system prompt", req.get("system_prompt")))
    out.append(_block("user message", req.get("assembled_user_message")))

    out.append(_section("2. RESPONSE RECEIVED FROM LLM1"))
    if res.get("error"):
        out.append(f"error      : {res['error']}")
    out.append(f"latency_ms : {res.get('latency_ms', '')}")
    out.append(
        f"tokens     : in={res.get('input_tokens', 0)} out={res.get('output_tokens', 0)}"
    )
    out.append(f"model_id   : {res.get('model_id', '')}")
    out.append(f"validation : {res.get('validation_errors', [])}")
    out.append(_block("raw model text", res.get("raw_model_text")))
    out.append(_block("parsed analytical execution plan (AEP)", res.get("parsed_aep")))

    out.append(_section("3. DETERMINISTIC RESOLVER OUTCOME"))
    if resolver:
        out.append("ran        : yes")
        out.append(f"errors     : {resolver.get('errors', [])}")
        out.append(_block("resolved execution plan (REP)", resolver.get("rep")))
        out.append(_block("confirmation summary", resolver.get("summary")))
    else:
        out.append("ran        : no (short-circuited before resolution)")

    out.append(_section("4. RESPONSE SENT TO USER"))
    out.append(f"phase      : {user_res.get('phase', '')}")
    out.append(_block("response body", user_res.get("body")))

    out.append("=" * _WIDTH)
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
