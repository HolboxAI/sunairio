"""Select and assemble schema slices for an analytics LLM2 call."""

from __future__ import annotations

from typing import Any, Dict, List

from analytics.llm2 import schemas


def build_schema_block(rep: Dict[str, Any]) -> str:
    """Markdown schema section for the LLM2 user message."""
    required = list(rep.get("required_schema") or [])
    routing = rep.get("routing") or {}

    # Ensure logical forecast families are present when routing says so.
    if routing.get("forecast_database") or routing.get("forecast_evolution"):
        category = ((rep.get("variable") or {}).get("category") or "").lower()
        if category == "weather" and "weather_forecast" not in required:
            required.append("weather_forecast")
        elif category != "weather" and "energy_forecast" not in required:
            # Energy + market share energy_forecast slice; market also gets prices.
            if "fundamental" in category or "price" in (
                (rep.get("variable") or {}).get("name") or ""
            ).lower():
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
