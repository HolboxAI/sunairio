"""v3 chart units bound from final SQL + user question."""

from core.models import ChartDetails
from planner.chart_units import bind_chart_units, parse_select_aliases
from planner.models import PlannerEnvelope


def _env(sql: str, y_axis, y_unit=None, question="Show the series"):
    return PlannerEnvelope(
        clarity_required=False,
        clarifying_question=None,
        question=question,
        understanding="test",
        answer_type="Sql",
        assumptions=[],
        final_sql=sql,
        chart_applicable=True,
        chart_details=ChartDetails(
            chart_type="line",
            x_axis=["valid_datetime"],
            y_axis=y_axis,
            x_unit=["UTC"],
            y_unit=y_unit or [""] * len(y_axis),
        ),
    )


def test_parse_select_aliases_with_cte():
    sql = (
        "WITH combined AS (SELECT valid_datetime, ensemble_value FROM t) "
        "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) "
        "AS p50_gsi FROM combined GROUP BY valid_datetime"
    )
    aliases = parse_select_aliases(sql)
    assert "p50_gsi" in aliases
    assert "valid_datetime" in aliases


def test_probability_is_not_mw():
    sql = (
        "SELECT valid_datetime, COUNT(*)::float / 1000.0 AS probability "
        "FROM energy_forecast_ensemble WHERE variable = 'load' GROUP BY 1"
    )
    env = _env(sql, ["probability"], y_unit=["MW"])
    bind_chart_units(
        env,
        user_question="probability load exceeds 80 GW",
        timezone="US/Eastern",
        units_map={"load": "MW"},
    )
    assert env.chart_details.y_unit == ["probability"]
    assert env.chart_details.x_unit == ["US/Eastern"]


def test_p50_load_uses_catalog_mw():
    sql = (
        "SELECT valid_datetime, percentile_disc(0.5) WITHIN GROUP (ORDER BY ensemble_value) "
        "AS p50_load FROM energy_forecast_ensemble WHERE variable = 'load' GROUP BY 1"
    )
    env = _env(sql, ["p50_load"], y_unit=["fraction"])
    bind_chart_units(env, units_map={"load": "MW", "gsi": "fraction"})
    assert env.chart_details.y_unit == ["MW"]


def test_gsi_stays_fraction():
    sql = (
        "SELECT valid_datetime, percentile_disc(0.9) WITHIN GROUP (ORDER BY ensemble_value) "
        "AS p90_gsi FROM energy_forecast_ensemble WHERE variable = 'gsi' GROUP BY 1"
    )
    env = _env(sql, ["p90_gsi"], y_unit=["MW"])
    bind_chart_units(env, units_map={"gsi": "fraction", "load": "MW"})
    assert env.chart_details.y_unit == ["fraction"]


def test_dual_series_load_and_temp():
    sql = (
        "SELECT valid_datetime, "
        "MAX(CASE WHEN variable = 'load' THEN ensemble_value END) AS load_mw, "
        "MAX(CASE WHEN variable = 'temp_2m' THEN ensemble_value END) AS temp_c "
        "FROM energy_forecast_ensemble GROUP BY 1"
    )
    env = _env(sql, ["load_mw", "temp_c"], y_unit=["MW", "MW"])
    bind_chart_units(env, units_map={"load": "MW", "temp_2m": "°C"})
    assert env.chart_details.y_unit == ["MW", "°C"]


def test_does_not_use_missing_sql_variable():
    sql = "SELECT valid_datetime, AVG(ensemble_value) AS avg_temp FROM t WHERE variable = 'temp_2m'"
    env = _env(sql, ["avg_temp"], y_unit=[""])
    bind_chart_units(env, units_map={"temp_2m": "°C", "load": "MW"})
    assert env.chart_details.y_unit == ["°C"]
