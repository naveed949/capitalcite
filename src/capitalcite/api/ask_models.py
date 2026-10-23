"""HTTP ask bodies and the typed SSE event union."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from capitalcite.agent.types import AskPath, PlanStep, TraceStep
from capitalcite.ask.types import AskModelName, Citation
from capitalcite.gate import DenyReason

_AUDITED_DENIES = frozenset(
    reason.value for reason in DenyReason if reason is not DenyReason.gate_error
)


class AskRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    question: str = Field(min_length=1, max_length=8000)
    k: int | None = Field(default=None, ge=1, le=50)
    ticker: str | None = None
    form: str | None = None
    period: str | None = None
    model: AskModelName | None = None
    path: AskPath = "rag"

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class AskResponse(BaseModel):
    kind: Literal["answered", "refused"]
    answer: str | None = None
    citations: list[Citation]
    retrieval_score: float
    refusal_reason: str | None = None
    config_hash: str
    collection_name: str
    point_count: int = Field(ge=0)
    path: AskPath = "rag"
    plan: list[PlanStep] = Field(default_factory=list)
    traces: list[TraceStep] = Field(default_factory=list)
    steps_used: int = Field(default=0, ge=0)
    tokens_used: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def legal_shape(self) -> AskResponse:
        if self.kind == "answered":
            if self.answer is None or not self.answer.strip():
                raise ValueError("answered response needs an answer")
            if not self.citations:
                raise ValueError("answered response needs citations")
            if self.refusal_reason is not None:
                raise ValueError("answered response must not include refusal_reason")
            return self
        if self.answer is not None:
            raise ValueError("refused response must not include an answer")
        if self.citations:
            raise ValueError("refused response must not include citations")
        if self.refusal_reason is None or not self.refusal_reason.strip():
            raise ValueError("refused response needs a reason")
        return self


class PlanEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["plan"] = "plan"
    plan: list[PlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def pending_only(self) -> PlanEvent:
        for step in self.plan:
            if step.status != "pending":
                raise ValueError("plan event steps are pending")
        return self


class ToolCallEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["tool_call"] = "tool_call"
    step: PlanStep

    @model_validator(mode="after")
    def pending_step(self) -> ToolCallEvent:
        if self.step.status != "pending":
            raise ValueError("tool_call step is pending")
        return self


class ToolResultEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["tool_result"] = "tool_result"
    trace: TraceStep


class ToolDeniedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["tool_denied"] = "tool_denied"
    trace: TraceStep
    reason: DenyReason

    @model_validator(mode="after")
    def audited_denial(self) -> ToolDeniedEvent:
        if self.trace.ok:
            raise ValueError("tool_denied trace is not ok")
        if self.reason is DenyReason.gate_error:
            raise ValueError("gate_error is not an audited denial")
        return self


class CitationEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["citation"] = "citation"
    citation: Citation


class AnswerDeltaEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["answer_delta"] = "answer_delta"
    text: str = Field(min_length=1)


class AnsweredEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["answered"] = "answered"
    response: AskResponse

    @model_validator(mode="after")
    def kind_matches(self) -> AnsweredEvent:
        if self.response.kind != "answered":
            raise ValueError("answered event requires an answered response")
        return self


class RefusedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["refused"] = "refused"
    response: AskResponse

    @model_validator(mode="after")
    def kind_matches(self) -> RefusedEvent:
        if self.response.kind != "refused":
            raise ValueError("refused event requires a refused response")
        return self


class ErrorEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["error"] = "error"
    status: Literal[422, 500, 503]
    detail: str = Field(min_length=1)


class DoneEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event: Literal["done"] = "done"


AskEvent = Annotated[
    PlanEvent
    | ToolCallEvent
    | ToolResultEvent
    | ToolDeniedEvent
    | CitationEvent
    | AnswerDeltaEvent
    | AnsweredEvent
    | RefusedEvent
    | ErrorEvent
    | DoneEvent,
    Field(discriminator="event"),
]


class AskFault(Exception):
    """``fold_response`` raises this. The route maps it to HTTP 422 or 503."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


def tool_fact(trace: TraceStep, reason: str | None) -> ToolResultEvent | ToolDeniedEvent:
    """Wrap the act node's trace. Do not build a second ``TraceStep``."""
    if reason is None or reason not in _AUDITED_DENIES:
        return ToolResultEvent(trace=trace)
    return ToolDeniedEvent(trace=trace, reason=DenyReason(reason))


def terminal_facts(response: AskResponse) -> list[AskEvent]:
    """Project one ``AskResponse`` into the frames that precede ``done``."""
    if response.kind == "refused":
        return [RefusedEvent(response=response)]
    text = response.answer
    if text is None:
        raise ValueError("answered response needs an answer")
    events: list[AskEvent] = [CitationEvent(citation=item) for item in response.citations]
    events.append(AnswerDeltaEvent(text=text))
    events.append(AnsweredEvent(response=response))
    return events


def encode_sse(event: AskEvent) -> bytes:
    payload = event.model_dump_json()
    if "\n" in payload or "\r" in payload:
        raise ValueError("sse data must be one line")
    return f"event: {event.event}\ndata: {payload}\n\n".encode()


async def fold_response(events: AsyncIterator[AskEvent]) -> AskResponse:
    """Return the terminal ``response``. Raise ``AskFault`` after an ``error`` event."""
    fault: ErrorEvent | None = None
    response: AskResponse | None = None
    async for event in events:
        if isinstance(event, AnsweredEvent | RefusedEvent):
            response = event.response
        elif isinstance(event, ErrorEvent):
            fault = event
    if fault is not None:
        raise AskFault(fault.status, fault.detail)
    if response is None:
        raise RuntimeError("ask stream ended without a response")
    return response


def event_model_names() -> tuple[str, ...]:
    union = get_args(AskEvent)[0]
    members = get_args(union)
    return tuple(model.model_fields["event"].default for model in members)


def ask_event_openapi_schema() -> dict[str, object]:
    """JSON Schema for the SSE union, with refs aimed at OpenAPI components."""
    schema = TypeAdapter(AskEvent).json_schema(ref_template="#/components/schemas/{model}")
    if not isinstance(schema, dict):
        raise TypeError("event schema must be an object")
    return schema


def hoist_openapi_defs(openapi: dict[str, object]) -> None:
    """Move ``$defs`` onto ``components.schemas`` and rewrite leftover ``#/$defs/`` refs."""
    components = openapi.setdefault("components", {})
    if not isinstance(components, dict):
        raise TypeError("openapi components must be an object")
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        raise TypeError("openapi components.schemas must be an object")

    def walk(node: object) -> None:
        if isinstance(node, dict):
            defs = node.pop("$defs", None)
            if isinstance(defs, dict):
                for name, spec in defs.items():
                    existing = schemas.get(name)
                    if existing is not None and existing != spec:
                        raise ValueError(f"openapi schema clash: {name}")
                    schemas[name] = spec
                    walk(spec)
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                node["$ref"] = "#/components/schemas/" + ref.removeprefix("#/$defs/")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(openapi)
    for spec in schemas.values():
        if not isinstance(spec, dict):
            continue
        properties = spec.get("properties")
        if not isinstance(properties, dict) or "event" not in properties:
            continue
        required = spec.setdefault("required", [])
        if isinstance(required, list) and "event" not in required:
            required.append("event")


def fault_detail(exc: BaseException) -> str:
    """One-line detail for an ``error`` event. ``encode_sse`` rejects newlines."""
    text = " ".join(str(exc).split())
    return text if text else type(exc).__name__
