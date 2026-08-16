"""LLM1 AEP parser tests."""

from analytics.llm1.parser import parse_aep, validate_aep


def test_parse_clarification_aep():
    raw = """
    {
      "status": "clarification_required",
      "clarification_questions": ["Which entity?", "P50 or mean?"],
      "assistant_message": "I need a couple of details.",
      "query": { "intent": "forecast" }
    }
    """
    aep = parse_aep(raw)
    assert aep.status == "clarification_required"
    assert len(aep.clarification_questions) == 2
    assert validate_aep(aep) == []


def test_parse_resolved_aep_from_fence():
    raw = """
    Here you go:
    ```json
    {
      "status": "resolved",
      "clarification_questions": [],
      "assistant_message": "Ready to confirm.",
      "query": {
        "intent": "forecast",
        "analysis_type": "time_series",
        "entity": {"role": "filter", "mode": "explicit", "values": ["ERCOT"]},
        "location": {"role": "filter", "mode": "explicit", "values": ["Houston"]},
        "variable": {"role": "filter", "mode": "explicit", "values": ["temp_2m"]},
        "timeframe": {"mode": "relative", "expression": "next_week"},
        "initialization": {"role": "filter", "mode": "latest", "values": []},
        "statistics": {"operation": "percentile", "value": 50},
        "visualization": {"required": true, "chart_type": "line"}
      },
      "notes": []
    }
    ```
    """
    aep = parse_aep(raw)
    assert aep.status == "resolved"
    assert aep.query.variable.values == ["temp_2m"]
    assert aep.query.timeframe.expression == "next_week"
    assert validate_aep(aep) == []


def test_parse_two_step_query_folds_historical_threshold():
    raw = """
    {
      "status": "resolved",
      "clarification_questions": [],
      "assistant_message": "I'll look up the 2023 PJM peak, then the probability tomorrow.",
      "query": [
        {
          "intent": "historical",
          "entity": {"role": "filter", "mode": "explicit", "values": ["PJM"]},
          "location": {"role": "filter", "mode": "logical_group", "values": ["RTO"]},
          "variable": {"role": "filter", "mode": "explicit", "values": ["load"]},
          "timeframe": {"mode": "explicit", "start": "2023-01-01", "end": "2023-12-31"},
          "statistics": {"operation": "max", "parameters": {}}
        },
        {
          "intent": "forecast",
          "analysis_type": "probability",
          "entity": {"role": "filter", "mode": "explicit", "values": ["PJM"]},
          "location": {"role": "filter", "mode": "logical_group", "values": ["RTO"]},
          "variable": {"role": "filter", "mode": "explicit", "values": ["load"]},
          "timeframe": {"mode": "relative", "expression": "tomorrow"},
          "statistics": {
            "operation": "probability",
            "parameters": {"direction": "above", "threshold": "{{step1.result}}"}
          }
        }
      ],
      "notes": []
    }
    """
    aep = parse_aep(raw)
    assert aep.status == "resolved"
    assert aep.query.intent == "forecast"
    assert aep.query.entity.values == ["PJM"]
    assert aep.query.location.values == ["RTO"]
    assert aep.query.variable.values == ["load"]
    assert aep.query.timeframe.expression == "tomorrow"
    params = aep.query.statistics.parameters
    assert params["threshold_source"] == "historical"
    assert params["threshold_statistic"] == "max"
    assert params["threshold_period"] == "2023"
    assert params["threshold_variable"] == "load"
    assert "threshold" not in params or params.get("threshold") != "{{step1.result}}"
    assert validate_aep(aep) == []


def test_validate_resolved_missing_variable():
    aep = parse_aep(
        """
        {
          "status": "resolved",
          "query": {
            "intent": "forecast",
            "entity": {"values": ["ERCOT"]},
            "location": {"values": ["Houston"]},
            "variable": {"values": []}
          }
        }
        """
    )
    errors = validate_aep(aep)
    assert any("variable" in e for e in errors)
