"""Tests for chart unit enrichment."""

from core.chart_units import enrich_chart_units, extract_variables_from_sql
from core.models import AgentEnvelope, ChartDetails, ConversationState
from data import metadata_db


def test_extract_variables_from_sql():
    sql = (
        "SELECT valid_datetime FROM energy_forecast_ensemble "
        "WHERE variable = 'wind_cap_fac' AND location IN ('south', 'west')"
    )
    assert extract_variables_from_sql(sql) == ["wind_cap_fac"]


def test_enrich_chart_units_backfills_y_unit(monkeypatch):
    monkeypatch.setattr(
        metadata_db,
        "get_variable_units",
        lambda: {"wind_cap_fac": "fraction"},
    )
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="P10 wind cap fac",
        answer_type="Sql",
        assumption=[],
        answer=(
            "SELECT valid_datetime, "
            "percentile_disc(0.10) WITHIN GROUP (ORDER BY ensemble_value) AS p10_south "
            "FROM energy_forecast_ensemble WHERE variable = 'wind_cap_fac'"
        ),
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=["p10_south", "p10_west"],
            x_unit=[""],
            y_unit=["", ""],
        ),
    )
    enrich_chart_units(envelope)
    assert envelope.chart_details.x_unit == ["UTC"]
    assert envelope.chart_details.y_unit == ["fraction", "fraction"]


def test_enrich_chart_units_preserves_llm_units(monkeypatch):
    monkeypatch.setattr(
        metadata_db,
        "get_variable_units",
        lambda: {"load": "MW"},
    )
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="Load",
        answer_type="Sql",
        assumption=[],
        answer="SELECT valid_datetime, AVG(ensemble_value) AS load_mw FROM t WHERE variable = 'load'",
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=["load_mw"],
            x_unit=["UTC"],
            y_unit=["MW"],
        ),
    )
    enrich_chart_units(envelope)
    assert envelope.chart_details.y_unit == ["MW"]


def test_enrich_uses_conversation_state_variable(monkeypatch):
    monkeypatch.setattr(
        metadata_db,
        "get_variable_units",
        lambda: {"gsi": "index"},
    )
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="GSI",
        answer_type="Sql",
        assumption=[],
        answer="SELECT valid_datetime, AVG(ensemble_value) AS gsi_avg FROM t",
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=["gsi_avg"],
            x_unit=[""],
            y_unit=[""],
        ),
    )
    state = ConversationState(variable="gsi")
    enrich_chart_units(envelope, state)
    assert envelope.chart_details.y_unit == ["index"]
