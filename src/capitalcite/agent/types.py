"""Plan steps, tool calls, budgets, and the agent result the HTTP layer flattens."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from capitalcite.ask.types import Answer
from capitalcite.retrieval.types import Hit

ToolName = Literal[
    "search_filings",
    "xbrl_fact",
    "fdic_bankfind",
    "calculator",
    "graph_query",
    "metric_query",
]
AskPath = Literal["rag", "agent"]
TOOL_NAMES: tuple[ToolName, ...] = (
    "search_filings",
    "xbrl_fact",
    "fdic_bankfind",
    "calculator",
    "graph_query",
    "metric_query",
)


class ToolCall(BaseModel):
    """One planned tool invocation. name is a string so unknown tools can be denied."""

    model_config = ConfigDict(frozen=True)
    name: str = Field(min_length=1)
    args: dict[str, object] = Field(default_factory=dict)


class PlannerDecision(BaseModel):
    """Untrusted planner output. Parse at the adapter. The graph still checks it."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["plan", "synthesize"]
    calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def legal_shape(self) -> PlannerDecision:
        if self.kind == "plan" and not self.calls:
            raise ValueError("plan needs at least one tool call")
        if self.kind == "synthesize" and self.calls:
            raise ValueError("synthesize must not include tool calls")
        return self


class PlanStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool: str = Field(min_length=1)
    args: dict[str, object] = Field(default_factory=dict)
    status: Literal["pending", "done", "error", "skipped"] = "pending"


class TraceStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool: str = Field(min_length=1)
    args: dict[str, object] = Field(default_factory=dict)
    ok: bool
    detail: str


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    tool: str
    args: dict[str, object]
    detail: str
    reason: str | None = None
    hits: tuple[Hit, ...] = ()
    value: str = ""


class Budgets(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_steps: int = Field(default=8, ge=1)
    max_tokens: int = Field(default=8000, ge=1)


class AgentResult(BaseModel):
    """Grounded answer plus the plan and tool trace. Routes flatten this."""

    model_config = ConfigDict(frozen=True)
    answer: Answer
    plan: tuple[PlanStep, ...] = ()
    traces: tuple[TraceStep, ...] = ()
    steps_used: int = Field(ge=0)
    tokens_used: int = Field(ge=0)
    stop_reason: str | None = None
