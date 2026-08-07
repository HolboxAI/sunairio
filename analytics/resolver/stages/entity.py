"""EntityResolver — business name → entity_id / shortname (ACL-checked)."""

from __future__ import annotations

from typing import Any, Dict, Set

from analytics.intent import is_awareness, is_metadata, needs_entity
from analytics.models import ResolvedEntity, ResolverContext
from analytics.text_match import normalize, phrase_overlap


def _names(ent: Dict[str, Any]) -> Set[str]:
    return {normalize(ent.get("entity")), normalize(ent.get("shortname"))} - {""}


def _label(ent: Dict[str, Any]) -> str:
    return str(ent.get("entity") or ent.get("shortname") or "?")


def _allowed_list(ctx: ResolverContext) -> str:
    return ", ".join(_label(e) for e in ctx.allowed_entities) or "(none)"


def resolve(ctx: ResolverContext) -> ResolverContext:
    intent = ctx.aep.query.intent
    dim = ctx.aep.query.entity
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    mode = (dim.mode or "explicit").lower()

    if is_awareness(intent):
        # Capability / access chat — no entity binding required
        return ctx

    if is_metadata(intent) and not values and mode == "metadata_query":
        # e.g. "which entities do I have?" — answered from catalog, not entity-scoped
        return ctx

    if not values:
        if needs_entity(intent, entity_mode=mode):
            ctx.errors.append(
                f"Which entity should this apply to? "
                f"You currently have access to: {_allowed_list(ctx)}."
            )
            ctx.unresolved.add("entity")
        return ctx

    raw = values[0]
    needle = normalize(raw)
    exact = [e for e in ctx.allowed_entities if needle in _names(e)]
    if exact:
        match = exact[0]
    else:
        partial = [
            e
            for e in ctx.allowed_entities
            if any(phrase_overlap(needle, name) for name in _names(e))
        ]
        if len(partial) > 1:
            options = ", ".join(_label(e) for e in partial)
            ctx.errors.append(
                f"'{raw}' could mean more than one entity you have access to ({options}). "
                "Which one did you mean?"
            )
            ctx.unresolved.add("entity")
            return ctx
        if not partial:
            ctx.errors.append(
                f"I couldn't match '{raw}' to an allowed entity. "
                f"You currently have access to: {_allowed_list(ctx)}."
            )
            ctx.unresolved.add("entity")
            return ctx
        match = partial[0]

    ctx.entity = ResolvedEntity(
        id=str(match["entity_id"]),
        name=str(match.get("shortname") or ""),
        display_name=str(match.get("entity") or match.get("shortname") or ""),
        timezone=str(match.get("timezone") or "UTC"),
    )
    return ctx
