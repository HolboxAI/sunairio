"""Tests for weather extended init walk-back."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analytics.weather_extended_init import (
    probe_location_from_context,
    resolve_weather_extended_init,
)
from analytics.models import ResolvedLocations


def test_probe_location_prefers_resolved_site():
    locs = ResolvedLocations(
        mode="explicit",
        count=1,
        values=[{"weather_sims_id": "houston", "location_name": "Houston"}],
    )
    assert (
        probe_location_from_context(
            "ercot_generic",
            {"ercot_generic": {"portfolio": {"weather_sims_id": "rto"}}},
            locs,
        )
        == "houston"
    )


def test_probe_location_falls_back_to_portfolio():
    assert (
        probe_location_from_context(
            "ercot_generic",
            {"ercot_generic": {"portfolio": {"weather_sims_id": "rto"}, "resources": []}},
            None,
        )
        == "rto"
    )


def test_resolve_walks_back_when_floor_empty(monkeypatch):
    short = datetime(2026, 8, 12, 7, 0, tzinfo=timezone.utc)
    calls = []

    def fake_has_rows(project, location, init, *, variable="temp_2m", ensemble_path=1):
        calls.append(init)
        if init.hour == 0 and init.day == 12:
            return True
        return False

    monkeypatch.setattr(
        "analytics.weather_extended_init.extended_init_has_rows",
        fake_has_rows,
    )
    resolved = resolve_weather_extended_init(
        short,
        project_name="ercot_generic",
        location="houston",
    )
    assert resolved == "2026-08-12T00:00:00Z"
    assert calls[0].hour == 6
    assert any(c.hour == 0 for c in calls)


def test_resolve_uses_floor_when_first_anchor_landed(monkeypatch):
    short = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)

    def fake_has_rows(project, location, init, *, variable="temp_2m", ensemble_path=1):
        return init.hour == 0

    monkeypatch.setattr(
        "analytics.weather_extended_init.extended_init_has_rows",
        fake_has_rows,
    )
    resolved = resolve_weather_extended_init(
        short,
        project_name="ercot_generic",
        location="houston",
    )
    assert resolved == "2026-08-12T00:00:00Z"


def test_resolve_falls_back_to_floor_without_db(monkeypatch):
    monkeypatch.setattr(
        "analytics.weather_extended_init.extended_init_has_rows",
        lambda *a, **k: None,
    )
    resolved = resolve_weather_extended_init(
        "2026-08-12T07:00:00Z",
        project_name="ercot_generic",
        location="houston",
    )
    assert resolved == "2026-08-12T06:00:00Z"
