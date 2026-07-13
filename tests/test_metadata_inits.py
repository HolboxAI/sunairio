"""Tests for latest_inits shaping and weather forecast_long anchor."""

from datetime import datetime, timezone

from data import metadata_db


def test_floor_weather_long_init_utc_grid():
    assert metadata_db.floor_weather_long_init(
        datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)
    assert metadata_db.floor_weather_long_init(
        datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)
    assert metadata_db.floor_weather_long_init(
        datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


def test_floor_weather_long_init_uses_utc_not_local():
    # 10:00 UTC = 06:00 US/Eastern (EDT); local 6h floor would be 10:00 UTC.
    eastern = datetime(2026, 7, 13, 10, 0, tzinfo=timezone.utc)
    assert metadata_db.floor_weather_long_init(eastern).hour == 6


def test_get_latest_inits_nested_adds_forecast_long(monkeypatch):
    flat = {
        ("ercot_generic", "weather", "forecast"): datetime(
            2026, 7, 13, 10, 0, tzinfo=timezone.utc
        ),
        ("ercot_generic", "energy", "forecast"): datetime(
            2026, 7, 13, 9, 0, tzinfo=timezone.utc
        ),
    }
    monkeypatch.setattr(metadata_db, "get_latest_inits_by_project", lambda force=False: flat)
    nested = metadata_db.get_latest_inits_nested(["ercot_generic"])
    weather = nested["ercot_generic"]["weather"]
    assert weather["forecast"] == "2026-07-13T10:00:00+00:00"
    assert weather["forecast_long"] == "2026-07-13T06:00:00+00:00"
    assert "forecast_long" not in nested["ercot_generic"]["energy"]
