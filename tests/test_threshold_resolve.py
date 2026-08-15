"""Tests for historical threshold pre-resolution before LLM2."""

from __future__ import annotations

import pytest

from analytics.threshold_resolve import (
    needs_historical_threshold_resolution,
    resolve_historical_threshold,
)


def _pjm_rep():
    return {
        "entity": {"display_name": "PJM", "name": "pjm_generic"},
        "locations": {
            "values": [{"energy_sims_id": "pjm", "location_name": "PJM"}],
        },
        "variable": {"name": "load"},
        "statistics": {
            "operation": "probability",
            "parameters": {
                "threshold_source": "historical",
                "threshold_variable": "load",
                "threshold_entity": "PJM",
                "threshold_location": "RTO",
                "threshold_period": "2023",
                "threshold_statistic": "max",
                "direction": "above",
            },
        },
    }


def test_needs_resolution_when_threshold_source_historical():
    assert needs_historical_threshold_resolution(_pjm_rep()) is True


def test_skips_when_threshold_already_numeric():
    rep = _pjm_rep()
    rep["statistics"]["parameters"]["threshold"] = 147187.0
    assert needs_historical_threshold_resolution(rep) is False


def test_resolve_patches_rep(monkeypatch):
    def fake_query(sql, params=None, request_id=None):
        assert params["iso"] == "PJM"
        assert params["region"] == "pjm"
        assert params["variable"] == "load"
        assert params["start"] == "2023-01-01"
        return {"rows": [[147187.487]]}

    monkeypatch.setattr(
        "analytics.threshold_resolve.metadata_db.execute_query", fake_query
    )
    patched, value = resolve_historical_threshold(_pjm_rep())
    assert value == pytest.approx(147187.487)
    assert patched["statistics"]["parameters"]["threshold"] == pytest.approx(147187.487)
    assert patched["statistics"]["parameters"]["threshold_resolved_from"]["operation"] == "max"
