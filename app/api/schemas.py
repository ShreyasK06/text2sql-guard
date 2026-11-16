"""Request and response models for the HTTP API (used by app/api/main.py)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.types import LAYERS


class QueryRequest(BaseModel):
    question: str | None = Field(default=None, max_length=2000)
    role: Literal["analyst", "manager", "admin"]
    region: Literal["north", "south", "east", "west"] | None = None
    sql: str | None = Field(default=None, max_length=20000)  # direct SQL mode (sql_override)
    layers: list[str] | None = None                          # None = all six layers

    @field_validator("layers")
    @classmethod
    def _known_layers(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        unknown = [x for x in v if x not in LAYERS]
        if unknown:
            raise ValueError(f"unknown layer(s) {unknown}; valid: {list(LAYERS)}")
        return v

    @model_validator(mode="after")
    def _need_input(self) -> "QueryRequest":
        if not (self.question and self.question.strip()) and not (self.sql and self.sql.strip()):
            raise ValueError("provide a question, a sql string, or both")
        return self


class QueryResponse(BaseModel):
    request_id: str
    decision: str
    blocked_by: str | None = None
    rule_id: str | None = None
    reason: str = ""
    sql_generated: str | None = None
    sql_executed: str | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    masked_columns: list[str] = []
    redactions: int = 0
    retries: int = 0
    correction_log: list[dict[str, Any]] = []
    self_check: dict[str, Any] | None = None
    latency_ms: float = 0.0
    layer_latency_ms: dict[str, float] = {}
    guard_results: list[dict[str, Any]] = []
    config: str = "full"


class HealthResponse(BaseModel):
    status: str
    db: bool
    provider: str


class PolicyResponse(BaseModel):
    role: str
    description: str
    row_scope: str
    plain_columns: dict[str, list[str]]
    masked_columns: dict[str, dict[str, str]]
    visible_tables: list[str]


class CatalogResponse(BaseModel):
    roles: list[dict[str, str]]
    schema_: dict[str, list[str]] = Field(alias="schema")
    sensitive_columns: dict[str, str]
    limits: dict[str, int]

    model_config = {"populate_by_name": True}
