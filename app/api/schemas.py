"""Pydantic API models."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class ClearRequest(BaseModel):
    session_id: str


class SqlRequest(BaseModel):
    """Run a SQL statement directly, bypassing the LLM."""

    sql: str


class QueryData(BaseModel):
    columns: List[str]
    rows: List[List[Any]]
    row_count: int
    truncated: bool = False
    query_time_ms: float
    backend: str


class ChartDetails(BaseModel):
    chart_type: Literal["line", "scatter", "bar"]
    x_axis: List[str]
    y_axis: List[str]
    x_unit: List[str] = []
    y_unit: List[str] = []
    series_column: Optional[str] = None
    dual_axis: bool = False
    display_columns: Optional[List[str]] = None


class LlmUsage(BaseModel):
    model_id: str
    input_tokens: int
    output_tokens: int


class QueryResponse(BaseModel):
    request_id: str
    session_id: str
    request_time: str  # UTC ISO-8601, e.g. 2026-07-12T05:48:00.123456+00:00
    response_time: str  # UTC ISO-8601
    clarity_required: bool
    clarifying_question: Optional[List[str]] = None
    question: str
    original_question: str
    answer_type: str
    assumption: List[str] = []
    suggestions: List[str] = []
    answer: Optional[str] = None
    result_template: Optional[str] = None
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    timezone: Optional[str] = None
    data: Optional[QueryData] = None
    result_summary: Optional[str] = None
    context_warnings: List[str] = []
    llm_usage: Optional[LlmUsage] = None


class ExecuteQueryRequest(BaseModel):
    """Run generated SQL after the user confirms via the Execute button."""

    sql: str
    answer_type: str = "Sql"
    question: Optional[str] = None
    result_template: Optional[str] = None


class ExecuteQueryResponse(BaseModel):
    request_id: str
    request_time: str
    response_time: str
    data: QueryData
    result_summary: Optional[str] = None
    answer: Optional[str] = None
    context_warnings: List[str] = []


class SqlResponse(BaseModel):
    request_id: str
    request_time: str  # UTC ISO-8601
    response_time: str  # UTC ISO-8601
    sql: str
    plan: Optional[str] = None
    execution_detail: Optional[Dict[str, Any]] = None
    latency_ms: int
    data: QueryData


class HistorySessionItem(BaseModel):
    session_id: str
    title: str
    title_editable: bool = True
    updated_at: str
    turn_count: int


class HistorySessionListResponse(BaseModel):
    items: List[HistorySessionItem]


class HistorySessionTitleUpdate(BaseModel):
    title: str


class HistoryThreadResponse(BaseModel):
    session_id: str
    title: str
    turns: List[Dict[str, Any]]


class HistoryHydrateRequest(BaseModel):
    """Resume a stored conversation into the live in-memory LLM context."""

    session_id: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    metadata_username: Optional[str] = None


class RegisterResponse(BaseModel):
    message: str
    user: Dict[str, Any]


class UsageSummary(BaseModel):
    cycle_start: str
    cycle_end: str
    base_limit: int
    bonus_tokens: int
    effective_limit: int
    used_input_tokens: int
    used_output_tokens: int
    used_tokens: int
    remaining_tokens: int
    query_count: int
    cycle_anchor_date: Optional[str] = None


class UsageBreakdownItem(BaseModel):
    label: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    query_count: int


class UsageResponse(BaseModel):
    summary: Optional[UsageSummary] = None
    breakdown: List[UsageBreakdownItem] = []
    status: Optional[str] = None


class SetTokenLimitRequest(BaseModel):
    base_monthly_limit: int


class IncreaseTokenLimitRequest(BaseModel):
    bonus_tokens: int


class AdminUserItem(BaseModel):
    id: int
    email: str
    role: str
    metadata_username: Optional[str] = None
    status: str
    created_at: str
    usage: Optional[UsageSummary] = None


class AdminUserListResponse(BaseModel):
    items: List[AdminUserItem]


# ── Analytics v2 (Phase 1: consult → confirm) ─────────────────


class AnalyticsConsultRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class AnalyticsConfirmRequest(BaseModel):
    session_id: str
    rep_id: str
    action: Literal["confirm", "reject"]


class AnalyticsLlmUsage(BaseModel):
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class AnalyticsConsultResponse(BaseModel):
    request_id: str
    session_id: str
    phase: Literal["clarify", "confirm", "confirmed", "answered", "error"]
    assistant_message: Optional[str] = None
    questions: List[str] = []
    summary: Optional[Dict[str, Any]] = None
    rep_id: Optional[str] = None
    rep_preview: Optional[Dict[str, Any]] = None
    notes: List[str] = []
    errors: List[str] = []
    llm_usage: Optional[AnalyticsLlmUsage] = None


class AnalyticsConfirmResponse(BaseModel):
    request_id: str
    session_id: str
    phase: Literal["confirmed", "answered", "clarify", "error"]
    rep_id: str
    message: str
    summary: Optional[Dict[str, Any]] = None
    sql: Optional[str] = None
    target: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    result_summary: Optional[str] = None
    errors: List[str] = []
    llm_usage: Optional[AnalyticsLlmUsage] = None
    execution: Optional[Dict[str, Any]] = None
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    timezone: Optional[str] = None
    assumptions: List[str] = []


class AnalyticsHistoryHydrateRequest(BaseModel):
    session_id: str


class AnalyticsHistoryThreadResponse(BaseModel):
    session_id: str
    title: str
    turns: List[Dict[str, Any]]
    pending_rep: Optional[Dict[str, Any]] = None

