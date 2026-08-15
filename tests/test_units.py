"""Unit preference and threshold conversion."""

from __future__ import annotations

import pytest

from analytics.models import (
    AnalyticalExecutionPlan,
    AnalyticalQuery,
    DimensionSpec,
    ResolvedVariable,
    ResolverContext,
    StatisticsSpec,
)
from analytics.units import (
    f_to_c,
    format_value_unit,
    normalize_unit,
    parse_threshold_from_message,
    resolve_threshold_context,
)


def _temp_ctx(*, user_message: str = "", threshold: float = 95, criteria: dict | None = None):
    aep = AnalyticalExecutionPlan(
        status="resolved",
        query=AnalyticalQuery(
            intent="forecast",
            analysis_type="probability",
            variable=DimensionSpec(
                values=["temp_2m"],
                criteria=criteria or {},
            ),
            statistics=StatisticsSpec(
                operation="probability",
                parameters={"threshold": threshold, "direction": "above"},
            ),
        ),
    )
    return ResolverContext(
        aep=aep,
        allowed_entities=[],
        latest_inits={},
        entity_catalog={},
        variable_catalog=[],
        variable=ResolvedVariable(
            name="temp_2m",
            display_name="2 m Air Temperature",
            unit="°F",
            category="weather",
            native_unit="°C",
            unit_conversion={"from": "°C", "to": "°F", "method": "linear"},
        ),
        statistics={
            "operation": "probability",
            "parameters": {"threshold": threshold, "direction": "above"},
        },
        user_message=user_message,
    )


def test_normalize_unit_fahrenheit_aliases():
    assert normalize_unit("F") == "°F"
    assert normalize_unit("degF") == "°F"
    assert normalize_unit("ºF") == "°F"
    assert normalize_unit("celsius") == "°C"


def test_parse_threshold_from_message():
    assert parse_threshold_from_message("above 95F for three hours") == (95.0, "°F")
    assert parse_threshold_from_message("above 95°F") == (95.0, "°F")
    assert parse_threshold_from_message("above 35C") == (35.0, "°C")


def test_resolve_threshold_95f_to_celsius_native():
    ctx = _temp_ctx(user_message="three consecutive hours above 95F")
    tc = resolve_threshold_context(ctx)
    assert tc is not None
    assert tc.display_text == "95°F"
    assert tc.native_unit == "°C"
    assert abs(tc.native_value - f_to_c(95)) < 0.1
    assert tc.conversion_applied is True
    assert "95°F" in tc.plan_sentence(variable_name="temp_2m")
    assert "35" in tc.plan_sentence(variable_name="temp_2m")


def test_resolve_threshold_already_in_celsius_param():
    ctx = _temp_ctx(user_message="above 95F", threshold=35.0)
    tc = resolve_threshold_context(ctx)
    assert tc is not None
    assert tc.display_text == "95°F"
    assert abs(tc.native_value - 35.0) < 0.1


def test_format_value_unit():
    assert format_value_unit(95, "°F") == "95°F"
    assert format_value_unit(35.0, "°C") == "35°C"
