"""Observation kinds and the span record tests and the file exporter observe."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ObservationKind(StrEnum):
    """Langfuse observation type for each span. The value is the SDK as_type."""

    ASK = "chain"
    AGENT = "agent"
    RETRIEVE = "retriever"
    GENERATE = "generation"
    TOOL = "tool"
    GATE = "guardrail"
    EVAL = "evaluator"


class SpanRecord(BaseModel):
    """One finished span. attributes is the allowlisted set after masking."""

    model_config = ConfigDict(frozen=True)
    name: str = Field(min_length=1)
    kind: ObservationKind
    trace_id: str = Field(min_length=1)
    span_id: str = Field(min_length=1)
    parent_id: str | None = None
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)
    status: Literal["ok", "error"] = "ok"
    duration_ms: float = Field(ge=0.0)


AttrValue = str | int | float | bool
