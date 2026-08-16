"""Analytics consult log holds the full pipeline, untruncated, in one file."""

from __future__ import annotations

from pathlib import Path

from observability import analytics_consult_log as consult_log


def test_consult_log_keeps_full_prompts_and_resolver_io(tmp_path, monkeypatch):
    monkeypatch.setattr(consult_log, "_log_dir", lambda: tmp_path)
    rid = "req_full"
    long_prompt = "PROMPT_START " + ("x" * 20000) + " PROMPT_END"
    catalog = [{"variable": f"var_{i}", "unit": "MW"} for i in range(50)]

    consult_log.start(rid, {"session_id": "s1", "user": "u@test", "user_message": "list ghi places"})
    consult_log.log_llm1_request(
        rid,
        {
            "model_id": "m",
            "system_prompt": long_prompt,
            "system_prompt_hash": consult_log.prompt_hash(long_prompt),
            "assembled_user_message": "USER_MSG_START " + ("y" * 5000) + " USER_MSG_END",
            "history_turns": 2,
        },
    )
    consult_log.log_llm1_response(
        rid,
        {
            "raw_model_text": '{"intent":"metadata"}',
            "parsed_aep": {"query": {"intent": "metadata", "variable": {"values": ["ghi"]}}},
            "input_tokens": 10,
            "output_tokens": 20,
        },
    )
    consult_log.log_resolver(
        rid,
        {
            "input": {
                "aep": {"query": {"intent": "metadata"}},
                "user_message": "list ghi places",
                "variable_catalog": catalog,
                "entity_catalog": {"ercot_generic": {"resources": [{"resource_name": "Houston"}]}},
            },
            "errors": [],
            "rep": {"entity": {"name": "ercot_generic"}},
            "summary": {"entity": "ERCOT"},
        },
    )
    consult_log.log_user_response(rid, {"phase": "answered", "body": {"assistant_message": "here"}})
    path = consult_log.write_consult_log(rid)
    assert path
    text = Path(path).read_text()
    assert "1. USER REQUEST" in text
    assert "list ghi places" in text
    assert "2. LLM1 INPUT REQUEST" in text
    assert long_prompt in text
    assert "USER_MSG_START " in text and " USER_MSG_END" in text
    assert "3. LLM1 OUTPUT RESPONSE" in text
    assert "4. RESOLVER INPUT" in text
    assert "var_49" in text
    assert "Houston" in text
    assert "5. RESOLVER OUTPUT" in text
    assert "6. RESPONSE TO USER (consult)" in text
    assert text.count("PROMPT_START") == 1


def test_confirm_appends_llm2_to_same_file_untruncated(tmp_path, monkeypatch):
    monkeypatch.setattr(consult_log, "_log_dir", lambda: tmp_path)
    rid = "req_confirm"
    consult_log.start(rid, {"session_id": "s1", "user": "u", "user_message": "p50 load"})
    consult_log.log_user_response(rid, {"phase": "confirm", "body": {"phase": "confirm"}})
    path = consult_log.write_consult_log(rid)
    assert path

    llm2_prompt = "LLM2_PROMPT_START " + ("z" * 8000) + " LLM2_PROMPT_END"
    rows = [[i, f"row-{i}"] for i in range(200)]
    appended = consult_log.append_confirm_log(
        rid,
        confirm_request_id="confirm1",
        payload={
            "llm2_request": {
                "system_prompt": llm2_prompt,
                "assembled_user_message": "LLM2_USER " + ("w" * 3000),
                "model_id": "sql-model",
            },
            "llm2_response": {
                "raw_model_text": '{"sql":"SELECT 1"}',
                "parsed_plan": {"sql": "SELECT 1", "target": "forecast"},
            },
            "executor": {
                "sql": "SELECT 1",
                "data": {"columns": ["n", "label"], "rows": rows, "row_count": 200},
            },
            "confirm_response": {"phase": "answered", "message": "done", "data": {"rows": rows}},
        },
    )
    assert appended == path
    text = Path(path).read_text()
    assert "7. LLM2 INPUT REQUEST" in text
    assert llm2_prompt in text
    assert "8. LLM2 OUTPUT RESPONSE" in text
    assert "SELECT 1" in text
    assert "row-199" in text
    assert "10. RESPONSE TO USER (confirm)" in text
    assert "1. USER REQUEST" in text
