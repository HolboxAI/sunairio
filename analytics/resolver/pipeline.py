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
    location.resolve,
    variable.resolve,
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
    entity_variables: Optional[Dict[str, Dict[str, Any]]] = None,
    user_message: str = "",
    session_slots: Optional[Dict[str, str]] = None,
    session_refs: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[ResolvedExecutionPlan], Optional[ConfirmationSummary], List[str]]:
    """Run all stages. Returns (rep, summary, errors)."""
    ctx = ResolverContext(
        aep=aep,
        allowed_entities=allowed_entities,
        latest_inits=latest_inits,
        entity_catalog=entity_catalog,
        variable_catalog=variable_catalog,
        entity_variables=entity_variables or {},
        current_utc=current_utc,
        user_message=(user_message or "").strip(),
        session_slots=dict(session_slots or {}),
        session_refs=list(session_refs or []),
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
