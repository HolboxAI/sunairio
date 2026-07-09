"""Build prompt §3 session context from user ACL and metadata."""

from __future__ import annotations

from datetime import datetime, timezone

from core.models import ConversationState, SessionContext
from data import metadata_db
from security.acl import UserACL


def build_session_context(
    user: dict,
    acl: UserACL,
    conversation_state: ConversationState,
) -> SessionContext:
    username = user.get("metadata_username") or user.get("email") or ""
    entity_ids = acl.entity_ids if not acl.is_admin else _all_entity_ids()
    allowed_entities = metadata_db.load_allowed_entities(entity_ids) if entity_ids else []
    shortnames = [e["shortname"] for e in allowed_entities if e.get("shortname")]
    latest_inits = metadata_db.get_latest_inits_nested(shortnames) if shortnames else {}
    catalog_entity_ids = [
        str(e["entity_id"]) for e in allowed_entities if e.get("entity_id")
    ]
    entity_catalog = (
        metadata_db.load_entity_catalog(catalog_entity_ids) if catalog_entity_ids else {}
    )
    return SessionContext(
        username=username,
        current_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        allowed_entities=allowed_entities,
        latest_inits=latest_inits,
        conversation_state=conversation_state,
        variable_units=metadata_db.get_variable_units(),
        entity_catalog=entity_catalog,
    )


def _all_entity_ids() -> list:
    try:
        from data.metadata_db import get_connection

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT entity_id FROM entities")
                return [str(r[0]) for r in cur.fetchall()]
    except Exception:
        return []


def to_prompt_json(ctx: SessionContext) -> dict:
    return ctx.to_dict()
