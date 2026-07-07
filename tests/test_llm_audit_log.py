"""Tests for LLM audit log bundle."""

import json
from pathlib import Path

from observability.llm_audit_log import log_llm_request, log_llm_response, write_audit_bundle


def test_audit_bundle_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("observability.llm_audit_log._audit_dir", lambda: tmp_path)
    rid = "abc123"
    log_llm_request(rid, {"session_context": {"username": "u"}})
    log_llm_response(rid, {"raw_model_text": "{}"})
    path = write_audit_bundle(rid)
    assert path is not None
    data = json.loads(Path(path).read_text())
    assert "before" in data and "after" in data
    assert data["request_id"] == rid
