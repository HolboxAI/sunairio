"""Actionable diagnostics when forecast/historical queries return zero rows."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def diagnose_zero_rows(
    rep: Dict[str, Any],
    *,
    sql: Optional[str] = None,
) -> List[str]:
    """Return human-readable checks — not auto-retries."""
    hints: List[str] = []
    if not isinstance(rep, dict):
        return ["No execution plan was available to diagnose the empty result."]

    locs = ((rep.get("locations") or {}).get("values") or [])[:3]
    loc_labels = [
        (loc.get("location_name") or loc.get("weather_sims_id") or loc.get("energy_sims_id") or "")
        for loc in locs
        if isinstance(loc, dict)
    ]
    loc_labels = [x for x in loc_labels if x]
    tf = rep.get("timeframe") or {}
    start = str(tf.get("start") or "")
    end = str(tf.get("end") or "")
    init = rep.get("initialization") or {}
    init_resolved = str(init.get("resolved") or "")
    init_extended = str(init.get("resolved_extended") or "")
    var = rep.get("variable") or {}
    var_name = str(var.get("name") or "")
    category = str(var.get("category") or "").lower()
    routing = rep.get("routing") or {}

    hints.append(
        "The query ran successfully but returned no rows — nothing matched the "
        "location, initialization, variable, and time window together."
    )

    if loc_labels:
        hints.append(
            f"Location binding: {', '.join(loc_labels)} — verify the forecast table "
            f"uses the same location key (e.g. weather_sims_id)."
        )
    if init_resolved:
        hints.append(
            f"Initialization resolved to {init_resolved}. An older init or a horizon "
            f"beyond the short-range window (14 days) may leave gaps."
        )
    if (
        category == "weather"
        and init_extended
        and init_extended != init_resolved
        and routing.get("forecast_database")
    ):
        hints.append(
            f"Weather extended forecasts publish on a 6-hour UTC init grid — extended "
            f"table init should be {init_extended}, not {init_resolved}. Using the hourly "
            f"init on weather_forecast_ensemble_extended returns zero rows."
        )
    if start and end:
        hints.append(f"Requested period: {start} → {end}.")
        if category == "weather" and routing.get("forecast_database"):
            hints.append(
                "Weather short-range forecasts cover ~336 hours from initialization; "
                "dates far beyond that need the seasonal/extended tables."
            )

    if var_name == "temp_2m" and loc_labels:
        hints.append(
            "If this persists, try the exact catalog location name or a nearby load zone."
        )

    if sql and "houston" in sql.lower():
        hints.append(
            "SQL used location key 'houston' — confirm this matches the forecast DB "
            "column, not the display name 'Houston Load Zone'."
        )

    hints.append(
        "You can revise the plan (location, dates, or statistic) and I'll re-run."
    )
    return hints
