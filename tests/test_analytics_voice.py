"""Human voice helpers for resolver replies."""

from analytics.models import ConfirmationSummary, ResolvedEntity, ResolvedExecutionPlan, ResolvedInitialization, ResolvedLocations, ResolvedTimeframe, ResolvedVariable
from analytics.resolver.voice import (
    compose_clarify_message,
    compose_confirm_message,
    prefer_human_confirm_message,
)


def test_clarify_single_gap_sounds_human():
    msg = compose_clarify_message(
        ["Which entity should this apply to? You currently have access to: ERCOT, PJM."]
    )
    assert "Almost there" in msg or msg.startswith("Which entity")
    assert "Entity is required" not in msg
    assert "{" not in msg


def test_clarify_multiple_gaps_as_bullets():
    msg = compose_clarify_message(
        [
            "Which entity should this apply to?",
            "Which variable should we analyze?",
        ]
    )
    assert "couple of details" in msg
    assert "•" in msg


def test_confirm_forecast_narrative():
    summary = ConfirmationSummary(
        analysis="Forecast (time series)",
        entity="ERCOT",
        locations="Houston",
        forecast_horizon="2026-08-10 → 2026-08-16",
        initialization="Latest Forecast",
        initialization_resolved="2026-08-05T18:00:00Z",
        forecast_representation="Median (P50)",
        chart="Line",
    )
    rep = ResolvedExecutionPlan(
        intent="forecast",
        analysis_type="time_series",
        entity=ResolvedEntity(id="1", name="ercot_generic", display_name="ERCOT"),
        locations=ResolvedLocations(mode="explicit", count=1, values=[], label="Houston"),
        variable=ResolvedVariable(name="temp_2m", display_name="2 m Air Temperature", unit="°C"),
        timeframe=ResolvedTimeframe(start="2026-08-10", end="2026-08-16"),
        initialization=ResolvedInitialization(mode="latest", resolved="2026-08-05T18:00:00Z", label="Latest"),
        statistics={"operation": "percentile", "value": 50},
        routing={},
        required_schema=[],
        visualization={},
    )
    msg = compose_confirm_message(summary, rep)
    assert "Does this look right" in msg
    assert "ERCOT" in msg
    assert "Median (P50)" in msg
    assert "10 Aug 2026" in msg
    assert "Entity is required" not in msg


def test_prefer_human_over_mechanical_llm1():
    summary = ConfirmationSummary(
        analysis="Metadata (metadata lookup)",
        entity="ERCOT",
        locations="Weather locations",
        forecast_horizon="N/A",
        initialization="N/A",
        initialization_resolved="N/A",
        forecast_representation="Catalog lookup",
        chart="None",
    )
    msg = prefer_human_confirm_message(
        "Retrieving all available weather locations for ERCOT.",
        summary,
    )
    assert "confirm" in msg.lower() or "look" in msg.lower()
    assert not msg.lower().startswith("retrieving")
