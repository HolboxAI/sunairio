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
    # Every stage runs even after an earlier gap, so the user gets all the
    # open questions in one turn instead of one per round trip. Stages that
    # depend on an unresolved section stay quiet via ctx.unresolved.
    for stage in _STAGES:
        ctx = stage(ctx)
    if ctx.errors:
        return None, None, _dedupe(ctx.errors)
    return ctx.rep, ctx.summary, []


def _dedupe(errors: List[str]) -> List[str]:
    seen = set()
    out = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out
