"""InitializationResolver — latest / range / explicit → concrete timestamps."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from analytics.intent import is_awareness, is_metadata, normalize_intent
from analytics.models import ResolvedInitialization, ResolverContext
from analytics.weather_extended_init import (
    probe_location_from_context,
    resolve_weather_extended_init,
)
from data.metadata_db import floor_weather_long_init


def _pick_ensemble_bucket(category: str) -> str:
    cat = (category or "").lower()
    if cat == "weather":
        return "weather"
    if cat in ("energy", "market"):
        return "energy"
    return "weather"


def _latest_for_entity(ctx: ResolverContext) -> Optional[str]:
    if not ctx.entity:
        return None
    bucket = ctx.latest_inits.get(ctx.entity.name) or {}
    etype = _pick_ensemble_bucket(ctx.variable.category if ctx.variable else "")
    windows = bucket.get(etype) or {}
    # Prefer forecast window
    for key in ("forecast", "forecast_long", "short", "extended"):
        if windows.get(key):
            return str(windows[key])
    # Any window
    for val in windows.values():
        if val:
            return str(val)
    # Fallback other ensemble types
    for other in ("weather", "energy", "fundamental_market"):
        for val in (bucket.get(other) or {}).values():
            if val:
                return str(val)
    return None


def _normalize_ts(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    if s.endswith("Z"):
        return s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return s


def _weather_extended_init(ctx: ResolverContext, resolved: Optional[str]) -> Optional[str]:
    """UTC extended init: 6h floor with Forecast DB walk-back when lagging."""
    if not resolved or not ctx.entity:
        return None
    probe_loc = probe_location_from_context(
        ctx.entity.name,
        ctx.entity_catalog,
        ctx.locations,
    )
    var_name = (ctx.variable.name if ctx.variable else "") or "temp_2m"
    if probe_loc:
        try:
            return resolve_weather_extended_init(
                resolved,
                project_name=ctx.entity.name,
                location=probe_loc,
                variable=var_name,
            )
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        return floor_weather_long_init(dt).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _apply_weather_extended_init(ctx: ResolverContext, init: ResolvedInitialization) -> None:
    if not ctx.variable or (ctx.variable.category or "").lower() != "weather":
        return
    if init.mode in ("none", "metadata_query", "dimension"):
        return
    ext = _weather_extended_init(ctx, init.resolved)
    if ext and ext != init.resolved:
        init.resolved_extended = ext


def _unspecified(mode: str, values: list) -> bool:
    """True when LLM1 left initialization blank.

    DimensionSpec defaults `mode` to "explicit", so an omitted initialization
    arrives as explicit-with-no-values rather than an empty mode.
    """
    return mode in ("", "explicit") and not values


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = normalize_intent(ctx.aep.query.intent)
    dim = ctx.aep.query.initialization
    mode = (dim.mode or "").lower()
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]

    if is_awareness(intent) or is_metadata(intent):
        ctx.initialization = ResolvedInitialization(
            mode="none", resolved=None, values=[], label="N/A"
        )
        return ctx

    if intent in ("historical", "history") and (
        _unspecified(mode, values) or (mode == "latest" and not values)
    ):
        # Observations have no forecast initialization. Only honour one here when
        # the user explicitly asked for it (e.g. forecast-vs-actual comparisons).
        ctx.initialization = ResolvedInitialization(
            mode="none", resolved=None, values=[], label="Not applicable (historical)"
        )
        return ctx

    if _unspecified(mode, values):
        mode = "latest"

    if mode == "none":
        ctx.initialization = ResolvedInitialization(
            mode="none", resolved=None, values=[], label="N/A"
        )
        return ctx

    if mode == "latest":
        if not ctx.entity:
            # The entity stage already asked; don't stack a second question on it.
            ctx.unresolved.add("initialization")
            return ctx
        latest = _latest_for_entity(ctx)
        if not latest:
            ctx.errors.append(
                f"I couldn't find a latest initialization for "
                f"{ctx.entity.display_name if ctx.entity else 'that entity'} yet. "
                "Would you like to pick a specific initialization time instead?"
            )
            ctx.unresolved.add("initialization")
            return ctx
        ctx.initialization = ResolvedInitialization(
            mode="latest",
            resolved=_normalize_ts(latest),
            values=[_normalize_ts(latest)],
            label="Latest Forecast",
        )
        _apply_weather_extended_init(ctx, ctx.initialization)
        return ctx

    if mode == "explicit":
        if not values:
            ctx.errors.append(
                "You asked for a specific initialization — could you share the timestamp?"
            )
            ctx.unresolved.add("initialization")
            return ctx
        normalized = [_normalize_ts(v) for v in values]
        ctx.initialization = ResolvedInitialization(
            mode="explicit",
            resolved=normalized[0],
            values=normalized,
            label=", ".join(normalized),
        )
        _apply_weather_extended_init(ctx, ctx.initialization)
        return ctx

    if mode == "range":
        criteria = dim.criteria or {}
        start = criteria.get("from") or criteria.get("start") or (values[0] if values else None)
        end = criteria.get("to") or criteria.get("end") or (values[1] if len(values) > 1 else None)
        if not start or not end:
            ctx.errors.append(
                "For an initialization range, what start and end dates should I use?"
            )
            ctx.unresolved.add("initialization")
            return ctx
        # Phase 1: record the range; full enumeration of inits can expand in Phase 2
        ctx.initialization = ResolvedInitialization(
            mode="range",
            resolved=None,
            values=[str(start), str(end)],
            label=f"{start} → {end}",
        )
        return ctx

    if mode in ("comparison", "metadata_query"):
        ctx.initialization = ResolvedInitialization(
            mode=mode,
            resolved=None,
            values=values,
            label=mode.replace("_", " ").title(),
        )
        return ctx

    if mode == "dimension":
        # Forecast evolution — initialization is the analysis dimension
        ctx.initialization = ResolvedInitialization(
            mode="dimension",
            resolved=None,
            values=values,
            label="Initialization dimension",
        )
        return ctx

    ctx.errors.append(
        "How should we choose the forecast initialization — "
        "latest, a specific time, or a range?"
    )
    ctx.unresolved.add("initialization")
    return ctx
