"""Tests for response parser."""

from core.response_parser import parse_envelope, validate_envelope


def test_parse_envelope_sql():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "Test",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT 1"
    }"""
    env = parse_envelope(raw)
    assert env.clarity_required is False
    assert env.answer_type == "Sql"
    assert env.answer == "SELECT 1"
    assert validate_envelope(env) == []


def test_parse_envelope_clarify():
    raw = """{
      "clarity_required": true,
      "clarifying_question": ["Which project?"],
      "question": "GSI probability",
      "answer_type": "Sql",
      "assumption": [],
      "answer": null
    }"""
    env = parse_envelope(raw)
    assert env.clarity_required is True
    assert env.clarifying_question == ["Which project?"]
    assert validate_envelope(env) == []


def test_clarifying_question_string_coercion():
    raw = """{
      "clarity_required": true,
      "clarifying_question": "Which project?",
      "question": "GSI",
      "answer_type": "Sql",
      "assumption": [],
      "answer": null
    }"""
    env = parse_envelope(raw)
    assert env.clarifying_question == ["Which project?"]


def test_parse_envelope_line_chart():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "P90 GSI over 14 days",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT valid_datetime, percentile_disc(0.90) WITHIN GROUP (ORDER BY ensemble_value) AS p90_gsi FROM t",
      "chart_applicable": true,
      "chart_details": {
        "chart_type": "line",
        "x_axis": ["valid_datetime"],
        "y_axis": ["p90_gsi", "p10_gsi"],
        "x_unit": ["UTC"],
        "y_unit": ["", ""]
      }
    }"""
    env = parse_envelope(raw)
    assert env.chart_applicable is True
    assert env.chart_details is not None
    assert env.chart_details.chart_type == "line"
    assert env.chart_details.y_axis == ["p90_gsi", "p10_gsi"]
    assert validate_envelope(env) == []


def test_parse_envelope_peak_no_chart():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "Peak GSI",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT valid_datetime, probability FROM t LIMIT 1",
      "chart_applicable": false,
      "chart_details": null
    }"""
    env = parse_envelope(raw)
    assert env.chart_applicable is False
    assert env.chart_details is None
    assert validate_envelope(env) == []


def test_chart_required_when_applicable():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "P90 GSI",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT 1",
      "chart_applicable": true,
      "chart_details": null
    }"""
    env = parse_envelope(raw)
    errors = validate_envelope(env)
    assert any("chart_details" in e for e in errors)


def test_chart_coerces_single_string_axes():
    raw = """{
      "clarity_required": false,
      "clarifying_question": null,
      "question": "Scatter",
      "answer_type": "Sql",
      "assumption": [],
      "answer": "SELECT temp_2m, load FROM t",
      "chart_applicable": true,
      "chart_details": {
        "chart_type": "scatter",
        "x_axis": "temp_2m",
        "y_axis": "load"
      }
    }"""
    env = parse_envelope(raw)
    assert env.chart_details.chart_type == "scatter"
    assert env.chart_details.x_axis == ["temp_2m"]
    assert env.chart_details.y_axis == ["load"]
    assert env.chart_details.x_unit == [""]
    assert env.chart_details.y_unit == [""]
    assert validate_envelope(env) == []
