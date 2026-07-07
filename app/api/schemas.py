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


class ChartDetails(BaseModel):
    chart_type: Literal["line", "scatter", "bar"]
    x_axis: List[str]
    y_axis: List[str]
    x_unit: List[str] = []
    y_unit: List[str] = []


class QueryResponse(BaseModel):
    request_id: str
    session_id: str
    clarity_required: bool
    clarifying_question: Optional[List[str]] = None
    question: str
    answer_type: str
    assumption: List[str] = []
    answer: Optional[str] = None
    chart_applicable: bool = False
    chart_details: Optional[ChartDetails] = None
    context_warnings: List[str] = []
