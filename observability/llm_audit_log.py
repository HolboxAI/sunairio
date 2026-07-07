"""Before/after LLM audit logging for prompt comparison."""

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


def _audit_dir() -> Path:
    d = Path(settings.llm_audit_log_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompt_hash(system_prompt: str) -> str:
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def log_llm_request(request_id: str, payload: Dict[str, Any]) -> None:
    bundle = _bundles.setdefault(request_id, {})
    bundle["before"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def log_llm_response(request_id: str, payload: Dict[str, Any]) -> None:
    bundle = _bundles.setdefault(request_id, {})
    bundle["after"] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }


def write_audit_bundle(request_id: str) -> Optional[str]:
    bundle = _bundles.pop(request_id, None)
    if not bundle:
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f")
    path = _audit_dir() / f"{ts}_{request_id}.json"
    record = {"request_id": request_id, **bundle}
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    logger.info("LLM audit bundle written: %s", path)
    return str(path)


def get_bundle(request_id: str) -> Optional[Dict[str, Any]]:
    return _bundles.get(request_id)
