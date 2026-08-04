"""Tests for human-readable result summary filling."""

from core.response_parser import parse_envelope, validate_envelope
from core.result_summary import (
    build_metadata_answer,
    build_result_summary,
    build_scalar_fallback,
    fill_result_template,
)


def test_fill_result_template_case_insensitive():
    filled = fill_result_template(
        "The probability of simultaneous low wind and solar for Whole ERCOT is {PROBABILITY_BOTH_LOW}.",
        ["probability_both_low"],
        [0.094875],
    )
    assert filled == (
        "The probability of simultaneous low wind and solar for Whole ERCOT is 0.094875."
    )


def test_fill_result_template_value_alias():
    filled = fill_result_template(
        "Answer: {value}",
        ["probability_both_low"],
        [0.25],
    )
    assert filled == "Answer: 0.25"


def test_fill_result_template_missing_placeholder_returns_none():
    assert (
        fill_result_template(
            "Probability is {missing_col}.",
            ["probability_both_low"],
            [0.1],
        )
        is None
    )


def test_build_result_summary_prefers_template():
    summary = build_result_summary(
        question="How likely are both low?",
        result_template="The probability of simultaneous low of wind and solar for Whole ERCOT is {PROBABILITY_BOTH_LOW}.",
        columns=["PROBABILITY_BOTH_LOW"],
        rows=[[0.094875]],
    )
    assert summary == (
        "The probability of simultaneous low of wind and solar for Whole ERCOT is 0.094875."
    )


def test_build_result_summary_fallback_without_template():
    summary = build_result_summary(
        question="How likely are daily wind speed and GHI both low in ERCOT RTO over the next 7 days?",
        result_template=None,
        columns=["probability_both_low"],
        rows=[[0.094875]],
    )
    assert "0.094875" in summary
    assert "probability both low" in summary.lower()


def test_build_result_summary_skips_multi_row():
    assert (
        build_result_summary(
            question="Trend",
            result_template="Day {local_date} is {prob}.",
            columns=["local_date", "prob"],
            rows=[["2026-08-03", 0.1], ["2026-08-04", 0.2]],
        )
        is None
    )


def test_build_metadata_answer_single_column():
    text = build_metadata_answer(
        question="What are the solar zones in ERCOT?",
        columns=["resource_name"],
        rows=[["Houston"], ["West"], ["North"]],
    )
    assert "Houston" in text
    assert "West" in text
    assert "North" in text
    assert "resource name values are" in text.lower()


def test_build_metadata_answer_multi_column():
    text = build_metadata_answer(
        question="Solar zones",
        columns=["resource_name", "energy_sims_id"],
        rows=[["Houston", "houston_cdr"], ["West", "west_cdr"]],
    )
    assert "Found 2 results:" in text
    assert "Houston" in text
    assert "houston_cdr" in text


def test_build_metadata_answer_empty():
    text = build_metadata_answer(
        question="What are the solar zones in ERCOT?",
        columns=["resource_name"],
        rows=[],
    )
    assert "No matching catalog rows" in text
    assert "solar zones" in text.lower()


def test_scalar_fallback_multi_column():
    text = build_scalar_fallback(
        "Peak load tomorrow in ERCOT",
        ["peak_load", "peak_hour"],
        [72000.5, 17],
    )
    assert "72000.5" in text
    assert "17" in text


def test_parse_envelope_result_template():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "Probability both low",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT 0.1 AS probability_both_low",
      "result_template": "The probability is {probability_both_low}.",
      "chart_applicable": false,
      "chart_details": null
    }"""
    env = parse_envelope(raw)
    assert env.result_template == "The probability is {probability_both_low}."
    assert validate_envelope(env) == []


def test_result_template_null_when_clarify():
    raw = """{
      "clarity_required": true,
      "clarifying_question": ["Which location?"],
      "question": "Probability",
      "answer_type": "Sql",
      "assumption": [],
      "answer": null,
      "result_template": "The probability is {p}."
    }"""
    env = parse_envelope(raw)
    errors = validate_envelope(env)
    assert any("result_template" in e for e in errors)
