"""EntityResolver — business name → entity_id / shortname (ACL-checked)."""

from __future__ import annotations

from analytics.models import ResolvedEntity, ResolverContext


def resolve(ctx: ResolverContext) -> ResolverContext:
    dim = ctx.aep.query.entity
    values = [str(v).strip() for v in (dim.values or []) if str(v).strip()]
    if not values:
        ctx.errors.append("Entity is required.")
        return ctx

    needle = values[0].lower()
    match = None
    for ent in ctx.allowed_entities:
        candidates = [
            str(ent.get("entity") or "").lower(),
            str(ent.get("shortname") or "").lower(),
        ]
        if needle in candidates or needle.replace(" ", "_") in candidates:
            match = ent
            break
        # Partial: "ERCOT" matches entity display "ERCOT"
        if any(needle in c or c in needle for c in candidates if c):
            match = ent
            break

    if not match:
        allowed = ", ".join(
            str(e.get("entity") or e.get("shortname")) for e in ctx.allowed_entities
        ) or "(none)"
        ctx.errors.append(f"Entity '{values[0]}' is not in your allowed list: {allowed}.")
        return ctx

    ctx.entity = ResolvedEntity(
        id=str(match["entity_id"]),
        name=str(match.get("shortname") or ""),
        display_name=str(match.get("entity") or match.get("shortname") or ""),
        timezone=str(match.get("timezone") or "UTC"),
    )
    return ctx
