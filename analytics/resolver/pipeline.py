"""Ordered resolver pipeline: AEP → REP + confirmation summary."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from analytics.models import (
    AnalyticalExecutionPlan,
    ConfirmationSummary,
    ResolvedExecutionPlan,
    ResolverContext,
)
from analytics.resolver.stages import (
    confirmation,
    entity,
    initialization,
    location,
    routing,
    schema_select,
    time,
    variable,
)

_STAGES = (
    entity.resolve,
    variable.resolve,
    location.resolve,
    time.resolve,
    initialization.resolve,
    routing.resolve,
    schema_select.resolve,
    confirmation.resolve,
)


def resolve_aep(
    aep: AnalyticalExecutionPlan,
    *,
    allowed_entities: List[Dict[str, Any]],
    latest_inits: Dict[str, Dict[str, Dict[str, str]]],
    entity_catalog: Dict[str, Dict[str, Any]],
    variable_catalog: List[Dict[str, Any]],
    current_utc: str,
) -> Tuple[Optional[ResolvedExecutionPlan], Optional[ConfirmationSummary], List[str]]:
    """Run all stages. Returns (rep, summary, errors)."""
    ctx = ResolverContext(
        aep=aep,
        allowed_entities=allowed_entities,
        latest_inits=latest_inits,
        entity_catalog=entity_catalog,
        variable_catalog=variable_catalog,
        current_utc=current_utc,
    )
    for stage in _STAGES:
        ctx = stage(ctx)
        if ctx.errors:
            # Still run remaining stages only if early hard errors — stop on first error list
            return None, None, list(ctx.errors)
    return ctx.rep, ctx.summary, list(ctx.errors)
