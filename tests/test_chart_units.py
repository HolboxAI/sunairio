"""Tests for chart unit enrichment."""

from core.chart_units import (
    enrich_chart_units,
    extract_variables_from_sql,
    resolve_query_timezone,
)
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


def test_resolve_query_timezone_from_entity():
    entities = [
        {
            "entity_id": "1",
            "entity": "PJM",
            "shortname": "pjm_generic",
            "timezone": "US/Eastern",
            "has_forecast": True,
        }
    ]
    state = ConversationState(entity_shortname="pjm_generic")
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="temp",
        answer_type="Sql",
        assumption=["Entity: pjm_generic (PJM)"],
        answer="SELECT 1",
    )
    assert resolve_query_timezone(entities, state, envelope) == "US/Eastern"


def test_resolve_query_timezone_from_assumptions():
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="temp",
        answer_type="Sql",
        assumption=[
            "August 2026 in US/Central: 2026-08-01 05:00:00+00 → 2026-09-01 05:00:00+00"
        ],
        answer="SELECT 1",
    )
    assert resolve_query_timezone([], None, envelope) == "US/Central"


def test_enrich_chart_units_uses_timezone_for_time_axis(monkeypatch):
    monkeypatch.setattr(
        metadata_db,
        "get_variable_units",
        lambda: {"temp_2m": "°C"},
    )
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="temp",
        answer_type="Sql",
        assumption=[],
        answer="SELECT valid_datetime, AVG(ensemble_value) AS avg_temp FROM t WHERE variable = 'temp_2m'",
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=["avg_temp"],
            x_unit=[""],
            y_unit=[""],
        ),
    )
    enrich_chart_units(envelope, timezone="US/Eastern")
    assert envelope.chart_details.x_unit == ["US/Eastern"]
    assert envelope.chart_details.y_unit == ["°C"]


def test_enrich_chart_units_overrides_utc_for_time_axis(monkeypatch):
    monkeypatch.setattr(
        metadata_db,
        "get_variable_units",
        lambda: {"load": "MW"},
    )
    envelope = AgentEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question="load",
        answer_type="Sql",
        assumption=[],
        answer="SELECT valid_datetime, AVG(ensemble_value) AS p50_load FROM t WHERE variable = 'load'",
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=["p50_load"],
            x_unit=["UTC"],
            y_unit=[""],
        ),
    )
    enrich_chart_units(envelope, timezone="US/Eastern")
    assert envelope.chart_details.x_unit == ["US/Eastern"]
