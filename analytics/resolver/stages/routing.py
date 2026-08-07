"""RoutingResolver — forecast / historical / forecast evolution flags."""

from __future__ import annotations

from analytics.models import ResolverContext


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = (ctx.aep.query.intent or "").lower().replace(" ", "_")
    analysis = (ctx.aep.query.analysis_type or "").lower().replace(" ", "_")

    forecast = False
    historical = False
    forecast_evolution = False
    metadata = False

    if intent in ("metadata", "metadata_lookup", "awareness"):
        metadata = True
    elif intent in ("forecast_evolution",) or analysis in ("forecast_evolution",):
        forecast_evolution = True
        forecast = True
    elif intent in ("historical", "history"):
        historical = True
    elif intent in ("forecast", "probability", "comparison", "ranking", "distribution"):
        forecast = True
    else:
        # Heuristic from timeframe / init
        if (ctx.aep.query.initialization.mode or "").lower() == "dimension":
            forecast_evolution = True
            forecast = True
        else:
            forecast = True

    ctx.routing = {
        "forecast_database": forecast and not metadata,
        "historical_database": historical,
        "forecast_evolution": forecast_evolution,
        "metadata": metadata,
    }
    return ctx
