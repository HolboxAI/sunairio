"""Parse and validate LLM2 SQL JSON envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from llm.parsing import parse_json

ALLOWED_TARGETS = frozenset({"metadata", "forecast", "unsupported"})


@dataclass
class Llm2Plan:
    sql: Optional[str]
    target: str
    assumptions: List[str] = field(default_factory=list)
    result_template: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sql": self.sql,
            "target": self.target,
            "assumptions": list(self.assumptions),
            "result_template": self.result_template,
            "notes": list(self.notes),
        }


def parse_llm2_plan(raw_text: str) -> Llm2Plan:
    data = parse_json(raw_text)
    if not isinstance(data, dict):
        raise ValueError("LLM2 response is not a JSON object")
    target = str(data.get("target") or "").strip().lower()
    sql_raw = data.get("sql")
    sql = None if sql_raw is None else str(sql_raw).strip()
    if sql == "":
        sql = None
    assumptions = data.get("assumptions") or []
    if isinstance(assumptions, str):
        assumptions = [assumptions]
    if not isinstance(assumptions, list):
        assumptions = []
    notes = data.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        notes = []
    template = data.get("result_template")
    if template is not None:
        template = str(template).strip() or None
    return Llm2Plan(
        sql=sql,
        target=target,
        assumptions=[str(a) for a in assumptions if str(a).strip()],
        result_template=template,
        notes=[str(n) for n in notes if str(n).strip()],
    )


def validate_llm2_plan(plan: Llm2Plan) -> List[str]:
    errors: List[str] = []
    if plan.target not in ALLOWED_TARGETS:
        errors.append(f"invalid target: {plan.target!r}")
    if plan.target == "unsupported":
        if plan.sql:
            errors.append("unsupported target must not include sql")
        return errors
    if not plan.sql:
        errors.append("sql is required unless target is unsupported")
        return errors
    upper = plan.sql.lstrip().upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        errors.append("sql must be SELECT or WITH")
    # Lake / glue blocked in this phase even if the model emits it.
    if "GLUE." in plan.sql.upper():
        errors.append("Lake/glue SQL is not enabled in this phase")
    return errors


def parse_and_validate(raw_text: str) -> Tuple[Llm2Plan, List[str]]:
    plan = parse_llm2_plan(raw_text)
    return plan, validate_llm2_plan(plan)
