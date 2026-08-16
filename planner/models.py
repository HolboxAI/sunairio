"""Domain models for the v3 query planner envelope."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.models import ChartDetails

VALID_TARGETS = {"metadata", "forecast", "lake"}
VALID_RETURN_TYPES = {"number", "string", "timestamp", "boolean"}
VALID_CARDINALITY = {"one", "many"}


@dataclass
class ColumnContract:
    type: str
    cardinality: str

    def to_dict(self) -> Dict[str, str]:
        return {"type": self.type, "cardinality": self.cardinality}

    @classmethod
    def from_dict(cls, data: Any) -> "ColumnContract":
        if not isinstance(data, dict):
            return cls(type="string", cardinality="one")
        return cls(
            type=str(data.get("type") or "string").strip().lower(),
            cardinality=str(data.get("cardinality") or "one").strip().lower(),
        )


@dataclass
class PlanStep:
    id: str
    purpose: str
    target: str
    sql: str
    depends_on: List[str] = field(default_factory=list)
    returns: Dict[str, ColumnContract] = field(default_factory=dict)
    resolved: Optional[Dict[str, Any]] = None
    bound_sql: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": self.id,
            "purpose": self.purpose,
            "target": self.target,
            "sql": self.sql,
            "depends_on": list(self.depends_on),
            "returns": {k: v.to_dict() for k, v in self.returns.items()},
        }
        if self.resolved is not None:
            payload["resolved"] = self.resolved
        if self.bound_sql is not None:
            payload["bound_sql"] = self.bound_sql
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        returns_raw = data.get("returns") or {}
        returns: Dict[str, ColumnContract] = {}
        if isinstance(returns_raw, dict):
            for key, val in returns_raw.items():
                returns[str(key)] = ColumnContract.from_dict(val)
        depends = data.get("depends_on") or []
        if not isinstance(depends, list):
            depends = []
        return cls(
            id=str(data.get("id") or "").strip(),
            purpose=str(data.get("purpose") or "").strip(),
            target=str(data.get("target") or "").strip().lower(),
            sql=str(data.get("sql") or "").strip(),
            depends_on=[str(x).strip() for x in depends if str(x).strip()],
            returns=returns,
            resolved=data.get("resolved") if isinstance(data.get("resolved"), dict) else None,
            bound_sql=str(data["bound_sql"]).strip() if data.get("bound_sql") else None,
        )


@dataclass
class QueryPlan:
    steps: List[PlanStep]
    final_step: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "final_step": self.final_step,
        }

    def step_map(self) -> Dict[str, PlanStep]:
        return {s.id: s for s in self.steps}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["QueryPlan"]:
        if not isinstance(data, dict):
            return None
        raw_steps = data.get("steps") or []
        if not isinstance(raw_steps, list):
            raw_steps = []
        steps = [PlanStep.from_dict(s) for s in raw_steps if isinstance(s, dict)]
        return cls(
            steps=steps,
            final_step=str(data.get("final_step") or "").strip(),
        )


@dataclass
class PlannerEnvelope:
    clarity_required: bool
    clarifying_question: Optional[List[str]]
    question: str
    understanding: Optional[str]
    answer_type: str
    assumptions: List[str]
    suggestions: List[str] = field(default_factory=list)
    answer: Optional[str] = None
    query_plan: Optional[QueryPlan] = None
    final_sql: Optional[str] = None
    result_template: Optional[str] = None
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    lookup_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clarity_required": self.clarity_required,
            "clarifying_question": self.clarifying_question,
            "question": self.question,
            "understanding": self.understanding,
            "answer_type": self.answer_type,
            "assumptions": list(self.assumptions),
            "suggestions": list(self.suggestions),
            "answer": self.answer,
            "query_plan": self.query_plan.to_dict() if self.query_plan else None,
            "final_sql": self.final_sql,
            "result_template": self.result_template,
            "chart_applicable": self.chart_applicable,
            "chart_details": self.chart_details.to_dict() if self.chart_details else None,
            "lookup_error": self.lookup_error,
        }
