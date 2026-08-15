"""Select and assemble schema slices for an analytics LLM2 call."""

from __future__ import annotations

from typing import Any, Dict, List

from analytics.llm2 import schemas


def build_schema_block(rep: Dict[str, Any]) -> str:
    """Markdown schema section for the LLM2 user message."""
    required = list(rep.get("required_schema") or [])
    routing = rep.get("routing") or {}

    rep_vars = list(rep.get("variables") or [])
    if not rep_vars and rep.get("variable"):
        rep_vars = [rep.get("variable")]

    categories = {
        str((v or {}).get("category") or "").lower()
        for v in rep_vars
        if isinstance(v, dict)
    }
    if not categories:
        categories = {str(((rep.get("variable") or {}).get("category") or "")).lower()}

    # Ensure logical forecast families are present when routing says so.
    if routing.get("forecast_database") or routing.get("forecast_evolution"):
        if "weather" in categories and "weather_forecast" not in required:
            required.append("weather_forecast")
        if any(c for c in categories if c != "weather") and "energy_forecast" not in required:
            var_names = [
                str((v or {}).get("name") or "").lower()
                for v in rep_vars
                if isinstance(v, dict)
            ]
            primary_name = str(((rep.get("variable") or {}).get("name") or "")).lower()
            names = var_names or [primary_name]
            if any("fundamental" in c or "price" in n for c, n in zip(categories, names)):
                if "fundamental_market_forecast" not in required:
                    required.append("fundamental_market_forecast")
            elif "energy_forecast" not in required:
                required.append("energy_forecast")

    if routing.get("historical_database"):
        var_name = ((rep.get("variable") or {}).get("name") or "").lower()
        stats = rep.get("statistics") or {}
        params = stats.get("parameters") if isinstance(stats.get("parameters"), dict) else {}
        if var_name == "historical_price" or params.get("price_column"):
            if "historical_iso_prices" not in required:
                required.append("historical_iso_prices")
        elif "historical_iso_load_gen" not in required and "historical_iso_prices" not in required:
            required.append("historical_iso_load_gen")

    # Lake / archive: inject stub only so the model knows not to use glue.*.
    if routing.get("forecast_evolution") or "forecast_archive" in required:
        if "forecast_archive" not in required:
            required.append("forecast_archive")

    parts: List[str] = ["## Relevant database schemas", ""]
    slices = schemas.slices_for(required)
    if not slices:
        parts.append("_No schema slices selected — refuse with target unsupported._")
    else:
        parts.extend(slices)

    uses_forecast = any(
        n in required
        for n in (
            "weather_forecast",
            "energy_forecast",
            "fundamental_market_forecast",
        )
    ) or routing.get("forecast_database")
    uses_metadata = any(
        n.startswith("historical_") or n in (
            "entities",
            "locations",
            "resources",
            "resource_types",
            "variables",
            "location_weights",
            "location_variables",
            "resource_variables",
        )
        for n in required
    ) or routing.get("historical_database") or routing.get("metadata")

    if uses_forecast:
        parts.extend(["", schemas.FORECAST_ROUTING_HINT.strip()])
    if uses_metadata:
        parts.extend(["", schemas.METADATA_ROUTING_HINT.strip()])

    # Always remind lake is off.
    parts.extend(["", schemas.SCHEMA_SLICES["lake"].strip()])
    return "\n".join(parts)
