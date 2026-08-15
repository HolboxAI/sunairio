"""Domain models for Analytical Execution Plan (AEP) and Resolved Execution Plan (REP)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Set

AepStatus = Literal["clarification_required", "resolved"]
RepStatus = Literal["pending", "confirmed", "rejected"]
ConsultPhase = Literal["clarify", "confirm", "confirmed", "answered", "error"]


@dataclass
class DimensionSpec:
    role: str = "filter"
    mode: str = "explicit"
    values: List[Any] = field(default_factory=list)
    criteria: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "mode": self.mode,
            "values": list(self.values),
            "criteria": dict(self.criteria),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DimensionSpec":
        if not isinstance(data, dict):
            return cls()
        values = data.get("values") or []
        if not isinstance(values, list):
            values = [values] if values else []
        criteria = data.get("criteria") or {}
        if not isinstance(criteria, dict):
            criteria = {}
        return cls(
            role=str(data.get("role") or "filter"),
            mode=str(data.get("mode") or "explicit"),
            values=list(values),
            criteria=dict(criteria),
        )


@dataclass
class TimeframeSpec:
    mode: str = "explicit"
    start: Optional[str] = None
    end: Optional[str] = None
    target: Optional[str] = None
    expression: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "start": self.start,
            "end": self.end,
            "target": self.target,
            "expression": self.expression,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "TimeframeSpec":
        if not isinstance(data, dict):
            return cls()
        return cls(
            mode=str(data.get("mode") or "explicit"),
            start=_opt_str(data.get("start")),
            end=_opt_str(data.get("end")),
            target=_opt_str(data.get("target")),
            expression=_opt_str(data.get("expression") or data.get("relative")),
        )


@dataclass
class StatisticsSpec:
    operation: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    value: Optional[Any] = None
    type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "operation": self.operation,
            "parameters": dict(self.parameters),
        }
        if self.value is not None:
            out["value"] = self.value
        if self.type is not None:
            out["type"] = self.type
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "StatisticsSpec":
        if not isinstance(data, dict):
            return cls()
        params = data.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        operation = data.get("operation") or data.get("type")
        # LLM1 sometimes puts the percentile under parameters.percentile / .p
        # instead of value — lift it so the resolver never sees "Percentile (None)".
        value = data.get("value") if "value" in data else None
        if value is None:
            for key in ("value", "percentile", "p", "n"):
                if params.get(key) is not None:
                    value = params.get(key)
                    break
        return cls(
            operation=str(operation) if operation else None,
            parameters=dict(params),
            value=value,
            type=_opt_str(data.get("type")),
        )


@dataclass
class VisualizationSpec:
    required: bool = False
    chart_type: Optional[str] = None
    x_axis: Dict[str, Any] = field(default_factory=dict)
    y_axis: List[Dict[str, Any]] = field(default_factory=list)
    legend: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required": self.required,
            "chart_type": self.chart_type,
            "x_axis": dict(self.x_axis),
            "y_axis": list(self.y_axis),
            "legend": self.legend,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VisualizationSpec":
        if not isinstance(data, dict):
            return cls()
        y_axis = data.get("y_axis") or []
        if isinstance(y_axis, dict):
            y_axis = [y_axis]
        if not isinstance(y_axis, list):
            y_axis = []
        x_axis = data.get("x_axis") or {}
        if not isinstance(x_axis, dict):
            x_axis = {"meaning": str(x_axis)} if x_axis else {}
        chart_type = data.get("chart_type") or data.get("chart")
        return cls(
            required=bool(data.get("required", False)),
            chart_type=str(chart_type).lower() if chart_type else None,
            x_axis=dict(x_axis),
            y_axis=[dict(y) if isinstance(y, dict) else {"meaning": str(y)} for y in y_axis],
            legend=_opt_str(data.get("legend")),
            notes=_opt_str(data.get("notes")),
        )


@dataclass
class AnalyticalQuery:
    intent: Optional[str] = None
    analysis_type: Optional[str] = None
    entity: DimensionSpec = field(default_factory=DimensionSpec)
    location: DimensionSpec = field(default_factory=DimensionSpec)
    variable: DimensionSpec = field(default_factory=DimensionSpec)
    timeframe: TimeframeSpec = field(default_factory=TimeframeSpec)
    initialization: DimensionSpec = field(default_factory=DimensionSpec)
    statistics: StatisticsSpec = field(default_factory=StatisticsSpec)
    comparison: Dict[str, Any] = field(default_factory=dict)
    visualization: VisualizationSpec = field(default_factory=VisualizationSpec)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "analysis_type": self.analysis_type,
            "entity": self.entity.to_dict(),
            "location": self.location.to_dict(),
            "variable": self.variable.to_dict(),
            "timeframe": self.timeframe.to_dict(),
            "initialization": self.initialization.to_dict(),
            "statistics": self.statistics.to_dict(),
            "comparison": dict(self.comparison),
            "visualization": self.visualization.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AnalyticalQuery":
        if not isinstance(data, dict):
            return cls()
        comparison = data.get("comparison") or {}
        if not isinstance(comparison, dict):
            comparison = {}
        # Architecture samples use forecast_valid_time interchangeably with timeframe
        timeframe_src = data.get("timeframe") or data.get("forecast_valid_time") or {}
        return cls(
            intent=_opt_str(data.get("intent")),
            analysis_type=_opt_str(data.get("analysis_type")),
            entity=DimensionSpec.from_dict(data.get("entity")),
            location=DimensionSpec.from_dict(data.get("location")),
            variable=DimensionSpec.from_dict(data.get("variable")),
            timeframe=TimeframeSpec.from_dict(timeframe_src),
            initialization=DimensionSpec.from_dict(data.get("initialization")),
            statistics=StatisticsSpec.from_dict(data.get("statistics")),
            comparison=dict(comparison),
            visualization=VisualizationSpec.from_dict(data.get("visualization")),
        )


@dataclass
class AnalyticalExecutionPlan:
    status: AepStatus
    clarification_questions: List[str] = field(default_factory=list)
    query: AnalyticalQuery = field(default_factory=AnalyticalQuery)
    notes: List[str] = field(default_factory=list)
    assistant_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "clarification_questions": list(self.clarification_questions),
            "query": self.query.to_dict(),
            "notes": list(self.notes),
            "assistant_message": self.assistant_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalyticalExecutionPlan":
        status_raw = str(data.get("status") or "clarification_required").strip().lower()
        if status_raw in ("resolved", "complete", "ready"):
            status: AepStatus = "resolved"
        else:
            status = "clarification_required"
        questions = data.get("clarification_questions") or data.get("clarifying_questions") or []
        if isinstance(questions, str):
            questions = [questions]
        if not isinstance(questions, list):
            questions = []
        notes = data.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        if not isinstance(notes, list):
            notes = []
        query_src = data.get("query") if isinstance(data.get("query"), dict) else data
        return cls(
            status=status,
            clarification_questions=[str(q) for q in questions if str(q).strip()],
            query=AnalyticalQuery.from_dict(query_src),
            notes=[str(n) for n in notes if str(n).strip()],
            assistant_message=_opt_str(data.get("assistant_message") or data.get("message")),
        )


@dataclass
class ResolvedEntity:
    id: str
    name: str
    display_name: str
    timezone: str = "UTC"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedVariable:
    name: str
    display_name: str
    unit: str = ""
    category: str = ""
    native_unit: str = ""
    unit_conversion: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        if not out.get("native_unit"):
            out.pop("native_unit", None)
        if not out.get("unit_conversion"):
            out.pop("unit_conversion", None)
        return out


def variable_to_rep_dict(var: "ResolvedVariable") -> Dict[str, Any]:
    """Serialize a resolved variable for LLM2, including sim routing key."""
    from analytics.multi_variable import location_key_for_category

    out = var.to_dict()
    out["location_key"] = location_key_for_category(var.category)
    return out


@dataclass
class ResolvedLocations:
    mode: str
    count: int
    values: List[Dict[str, Any]] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedTimeframe:
    start: str
    end: str
    mode: str = "explicit"
    expression: Optional[str] = None
    target: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedInitialization:
    mode: str
    resolved: Optional[str] = None
    resolved_extended: Optional[str] = None
    values: List[str] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedExecutionPlan:
    intent: str
    analysis_type: str
    entity: ResolvedEntity
    locations: ResolvedLocations
    variable: ResolvedVariable
    timeframe: ResolvedTimeframe
    initialization: ResolvedInitialization
    statistics: Dict[str, Any]
    routing: Dict[str, bool]
    required_schema: List[str]
    visualization: Dict[str, Any]
    comparison: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    variables: List[ResolvedVariable] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "intent": self.intent,
            "analysis_type": self.analysis_type,
            "entity": self.entity.to_dict(),
            "locations": self.locations.to_dict(),
            "variable": self.variable.to_dict(),
            "timeframe": self.timeframe.to_dict(),
            "initialization": self.initialization.to_dict(),
            "statistics": dict(self.statistics),
            "routing": dict(self.routing),
            "required_schema": list(self.required_schema),
            "visualization": dict(self.visualization),
            "comparison": dict(self.comparison),
            "notes": list(self.notes),
        }
        if self.variables:
            out["variables"] = [variable_to_rep_dict(v) for v in self.variables]
        return out


@dataclass
class ConfirmationSummary:
    """User-facing confirmation card fields (not raw JSON)."""

    analysis: str
    entity: str
    locations: str
    forecast_horizon: str
    initialization: str
    initialization_resolved: str
    forecast_representation: str
    chart: str
    notes: List[str] = field(default_factory=list)
    output_shape: str = ""
    computation_summary: str = ""
    user_intent_echo: str = ""
    plan_narrative: str = ""
    plan_questions: List[str] = field(default_factory=list)
    plan_terms: List[str] = field(default_factory=list)
    aggregation: str = ""
    output_grain: str = ""
    threshold_mode: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResolverContext:
    """Mutable working state passed through resolver stages."""

    aep: AnalyticalExecutionPlan
    allowed_entities: List[Dict[str, Any]]
    latest_inits: Dict[str, Dict[str, Dict[str, str]]]
    entity_catalog: Dict[str, Dict[str, Any]]
    variable_catalog: List[Dict[str, Any]]
    # shortname → {variables, weather, energy_by_resource_type}; empty skips the gate
    entity_variables: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current_utc: str = ""
    user_message: str = ""
    errors: List[str] = field(default_factory=list)
    # Sections a stage could not resolve, so dependent stages stay quiet
    # instead of piling on their own follow-up questions.
    unresolved: Set[str] = field(default_factory=set)
    entity: Optional[ResolvedEntity] = None
    variable: Optional[ResolvedVariable] = None
    variables: List[ResolvedVariable] = field(default_factory=list)
    locations: Optional[ResolvedLocations] = None
    timeframe: Optional[ResolvedTimeframe] = None
    initialization: Optional[ResolvedInitialization] = None
    routing: Dict[str, bool] = field(default_factory=dict)
    required_schema: List[str] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)
    visualization: Dict[str, Any] = field(default_factory=dict)
    comparison: Dict[str, Any] = field(default_factory=dict)
    summary: Optional[ConfirmationSummary] = None
    rep: Optional[ResolvedExecutionPlan] = None
    price_column: Optional[str] = None
    session_slots: Dict[str, str] = field(default_factory=dict)
    session_refs: List[Dict[str, Any]] = field(default_factory=list)


def _opt_str(val: Any) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s or None
