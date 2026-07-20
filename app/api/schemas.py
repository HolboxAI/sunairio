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
    answer_type: str
    assumption: List[str] = []
    answer: Optional[str] = None
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    timezone: Optional[str] = None
    data: Optional[QueryData] = None
    context_warnings: List[str] = []
    llm_usage: Optional[LlmUsage] = None


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
