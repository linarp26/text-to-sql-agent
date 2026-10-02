from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=1000)


class Attempt(BaseModel):
    attempt: int
    sql: str | None
    reasoning: str | None = None
    error: str | None = None
    error_type: str | None = None
    latency_ms: int


class AgentResponse(BaseModel):
    question: str
    success: bool
    sql: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    summary: str | None = None
    error: str | None = None
    attempts: list[Attempt] = []
    latency_ms: int = 0
