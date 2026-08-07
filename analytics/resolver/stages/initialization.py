"""InitializationResolver — latest / range / explicit → concrete timestamps."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from analytics.models import ResolvedInitialization, ResolverContext


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


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = (ctx.aep.query.intent or "").lower()
    if intent in ("metadata", "awareness", "metadata_lookup", "historical"):
        # Historical may still have init irrelevant; metadata never needs it
        if intent != "historical":
            ctx.initialization = ResolvedInitialization(
                mode="none",
                resolved=None,
                values=[],
                label="N/A",
            )
            return ctx

    dim = ctx.aep.query.initialization
    mode = (dim.mode or "latest").lower()
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]

    if mode in ("latest", ""):
        latest = _latest_for_entity(ctx)
        if not latest:
            ctx.errors.append(
                f"No latest initialization available for {ctx.entity.display_name if ctx.entity else 'entity'}."
            )
            return ctx
        ctx.initialization = ResolvedInitialization(
            mode="latest",
            resolved=_normalize_ts(latest),
            values=[_normalize_ts(latest)],
            label="Latest Forecast",
        )
        return ctx

    if mode == "explicit":
        if not values:
            ctx.errors.append("Explicit initialization requires at least one timestamp.")
            return ctx
        normalized = [_normalize_ts(v) for v in values]
        ctx.initialization = ResolvedInitialization(
            mode="explicit",
            resolved=normalized[0],
            values=normalized,
            label=", ".join(normalized),
        )
        return ctx

    if mode == "range":
        criteria = dim.criteria or {}
        start = criteria.get("from") or criteria.get("start") or (values[0] if values else None)
        end = criteria.get("to") or criteria.get("end") or (values[1] if len(values) > 1 else None)
        if not start or not end:
            ctx.errors.append("Initialization range requires from/to dates.")
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

    ctx.errors.append(f"Unsupported initialization mode '{mode}'.")
    return ctx
