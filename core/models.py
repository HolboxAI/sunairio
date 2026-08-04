"""Domain models for session context and LLM envelope."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

AnswerType = Literal["Sql", "Metadata", "Awareness"]
ChartType = Literal["line", "scatter", "bar"]


@dataclass
class ChartDetails:
    chart_type: str
    x_axis: List[str]
    y_axis: List[str]
    x_unit: List[str] = field(default_factory=list)
    y_unit: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "x_axis": self.x_axis,
            "y_axis": self.y_axis,
            "x_unit": self.x_unit,
            "y_unit": self.y_unit,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], chart_type: Optional[str] = None) -> "ChartDetails":
        ct = chart_type or data.get("chart_type")
        return cls(
            chart_type=str(ct).strip().lower() if ct else "",
            x_axis=_as_str_list(data.get("x_axis")),
            y_axis=_as_str_list(data.get("y_axis")),
            x_unit=_as_str_list(data.get("x_unit")),
            y_unit=_as_str_list(data.get("y_unit")),
        )


def _as_str_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


@dataclass
class ConversationState:
    entity_shortname: Optional[str] = None
    location_key: Optional[str] = None
    variable: Optional[str] = None
    timeframe: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "entity_shortname": self.entity_shortname,
            "location_key": self.location_key,
            "variable": self.variable,
            "timeframe": self.timeframe,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationState":
        return cls(
            entity_shortname=data.get("entity_shortname"),
            location_key=data.get("location_key"),
            variable=data.get("variable"),
            timeframe=data.get("timeframe"),
        )


@dataclass
class SessionContext:
    username: str
    current_utc: str
    allowed_entities: List[Dict[str, Any]]
    latest_inits: Dict[str, Dict[str, Dict[str, str]]]
    conversation_state: ConversationState
    variable_units: Dict[str, str] = field(default_factory=dict)
    entity_catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "username": self.username,
            "current_utc": self.current_utc,
            "allowed_entities": self.allowed_entities,
            "latest_inits": self.latest_inits,
            "conversation_state": self.conversation_state.to_dict(),
            "variable_units": self.variable_units,
            "entity_catalog": self.entity_catalog,
        }


@dataclass
class AgentEnvelope:
    clarity_required: bool
    clarifying_question: Optional[List[str]]
    question: str
    answer_type: AnswerType
    assumption: List[str]
    answer: Optional[str]
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    # Sentence with {sql_alias} placeholders; filled after execution. Never invent numbers.
    result_template: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clarity_required": self.clarity_required,
            "clarifying_question": self.clarifying_question,
            "question": self.question,
            "answer_type": self.answer_type,
            "assumption": self.assumption,
            "answer": self.answer,
            "chart_applicable": self.chart_applicable,
            "chart_details": self.chart_details.to_dict() if self.chart_details else None,
            "result_template": self.result_template,
        }
