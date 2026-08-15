"""Chart inference for analytics confirm results."""

from __future__ import annotations

from analytics.chart_infer import infer_chart_from_rep


def test_infer_chart_wide_format_single_variable():
    rep = {
        "analysis_type": "comparison",
        "entity": {"timezone": "US/Central"},
        "variable": {"unit": "°C"},
        "visualization": {"required": True, "chart": "line"},
    }
    data = {
        "columns": ["valid_datetime", "p50", "mean", "trimmed_mean"],
        "rows": [
            ["2026-08-17T05:00:00Z", 24.1, 24.5, 24.2],
            ["2026-08-17T06:00:00Z", 24.0, 24.3, 24.1],
        ],
    }
    applicable, details, tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details.get("series_column") is None
    assert details["y_axis"] == ["p50", "mean", "trimmed_mean"]


def test_infer_chart_dual_axis_multi_variable():
    rep = {
        "analysis_type": "time_series",
        "entity": {"timezone": "US/Central"},
        "variable": {"unit": "MWh"},
        "variables": [
            {"name": "solar_gen", "unit": "MWh", "category": "Energy"},
            {"name": "temp_2m_gen", "unit": "°C", "category": "Weather"},
        ],
        "visualization": {"required": True, "chart": "line", "dual_axis": True},
    }
    data = {
        "columns": ["hour", "solar_gen_p50", "temp_2m_gen_p50"],
        "rows": [
            ["2026-08-12T00:00:00Z", 100.0, 25.0],
            ["2026-08-12T01:00:00Z", 110.0, 24.0],
        ],
    }
    applicable, details, _tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details.get("dual_axis") is True
    assert details["y_axis"] == ["solar_gen_p50", "temp_2m_gen_p50"]
    assert details["y_unit"] == ["MWh", "°C"]


def test_infer_chart_long_format_multi_location():
    rep = {
        "analysis_type": "time_series",
        "entity": {"timezone": "US/Central"},
        "locations": {"count": 4, "label": "All Load Zones"},
        "variable": {"unit": "MW"},
        "visualization": {"required": True, "chart": "line", "legend": "Load zone"},
    }
    rows = []
    for hi, hour in enumerate(("2026-08-12T00:00:00Z", "2026-08-12T01:00:00Z")):
        for zi, (zone, base) in enumerate(
            (
                ("Houston Load Zone", 10000),
                ("North Load Zone", 12000),
                ("South Load Zone", 9000),
                ("West Load Zone", 8000),
            )
        ):
            rows.append([hour, zone, base + hi * 10 + zi])

    data = {
        "columns": ["hour", "location_name", "load_p50"],
        "rows": rows,
    }
    applicable, details, tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details.get("series_column") == "location_name"
    assert details["y_axis"] == ["load_p50"]
    assert details["x_axis"] == ["hour"]


def test_infer_chart_correlation_scatter_paired_rows():
    rep = {
        "analysis_type": "correlation",
        "entity": {"timezone": "US/Central"},
        "variables": [
            {"name": "temp_2m", "unit": "°C"},
            {"name": "solar_gen", "unit": "MWh"},
        ],
        "visualization": {"required": True, "chart": "scatter"},
    }
    data = {
        "columns": [
            "temp_2m_c",
            "solar_gen_mwh",
            "n_points",
            "pearson_r",
            "avg_solar_gen_mwh",
            "avg_temp_2m_c",
        ],
        "rows": [
            [29.5, 1200.0, 154000, 0.6808, 1255.42, 29.92],
            [30.1, 800.0, 154000, 0.6808, 1255.42, 29.92],
            [28.0, 1500.0, 154000, 0.6808, 1255.42, 29.92],
        ],
    }
    applicable, details, _tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details["chart_type"] == "scatter"
    assert details["x_axis"] == ["temp_2m_c"]
    assert details["y_axis"] == ["solar_gen_mwh"]
    assert details.get("dual_axis") is not True
    assert details["x_unit"] == ["°C"]
    assert details["y_unit"] == ["MWh"]


def test_infer_chart_correlation_scalar_row_no_chart():
    rep = {
        "analysis_type": "correlation",
        "visualization": {"required": True, "chart": "scatter"},
    }
    data = {
        "columns": ["pearson_r", "n_points"],
        "rows": [[0.68, 154000]],
    }
    applicable, details, _tz = infer_chart_from_rep(rep, data)
    assert applicable is False
    assert details is None


def test_infer_chart_probability_exceedance_only():
    """Probability queries should chart the exceedance series, not diagnostics."""
    rep = {
        "analysis_type": "probability",
        "entity": {"timezone": "US/Eastern"},
        "variable": {"unit": "MW"},
        "visualization": {
            "required": True,
            "chart": "line",
            "unit": "%",
            "y": "probability load exceeds 2023 PJM peak",
        },
    }
    data = {
        "columns": [
            "hour",
            "threshold_mw",
            "total_paths",
            "paths_above",
            "exceedance_probability_pct",
        ],
        "rows": [
            ["2026-08-13T17:00:00+00:00", 147187.487, 1000, 22, 2.2],
            ["2026-08-13T18:00:00+00:00", 147187.487, 1000, 9, 0.9],
        ],
    }
    applicable, details, _tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details["y_axis"] == ["exceedance_probability_pct"]
    assert details["y_unit"] == ["%"]
    assert details.get("dual_axis") is not True


def test_infer_chart_joint_probability_dunkelflaute():
    rep = {
        "analysis_type": "probability",
        "entity": {"timezone": "US/Central"},
        "variable": {"unit": "m/s"},
        "visualization": {
            "required": True,
            "chart": "line",
            "unit": "%",
            "dual_axis": True,
        },
    }
    data = {
        "columns": [
            "hour",
            "wind_100m_median_mps",
            "ghi_gen_median_wm2",
            "n_paths",
            "n_joint_low",
            "joint_low_wind_ghi_prob_pct",
        ],
        "rows": [
            ["2026-08-23T13:00:00+00:00", 5.82, 131.01, 1000, 1, 0.10],
            ["2026-08-23T14:00:00+00:00", 5.59, 327.39, 1000, 0, 0.0],
        ],
    }
    applicable, details, _tz = infer_chart_from_rep(rep, data)
    assert applicable is True
    assert details is not None
    assert details["y_axis"] == ["joint_low_wind_ghi_prob_pct"]
    assert details["y_unit"] == ["%"]
    assert details.get("dual_axis") is not True
    assert details.get("display_columns") == ["hour", "joint_low_wind_ghi_prob_pct"]
