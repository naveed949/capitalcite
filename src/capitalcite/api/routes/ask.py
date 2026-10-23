"""Cited answers over retrieved chunks. The process lifespan does not open Qdrant."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from capitalcite.agent import (
    AgentResult,
    AskPath,
    Budgets,
    PlanStep,
    TraceStep,
    build_agent_model,
    default_registry,
    run_agent,
)
from capitalcite.agent.audited import AuditingTools, request_trace_id
from capitalcite.agent.fdic import (
    BANKFIND_TIMEOUT,
    FakeFdicClient,
    FdicClient,
    bankfind_user_agent,
)
from capitalcite.agent.http import HttpxJsonClient
from capitalcite.agent.xbrl import FakeXbrlClient, XbrlClient
from capitalcite.api.routes.search import get_setup
from capitalcite.ask.models import FakeStructuredModel, StructuredModel, build_structured_model
from capitalcite.ask.service import ask
from capitalcite.ask.types import (
    Answer,
    Answered,
    AskModelName,
    Citation,
    ModelNotConfigured,
    ModelUnavailable,
)
from capitalcite.audit import AuditLog
from capitalcite.config import Settings, get_settings
from capitalcite.gate import Gate, load_policy
from capitalcite.ingest.rate_limit import shared_limiter
from capitalcite.kg import open_store
from capitalcite.retrieval import (
    IndexNotBuilt,
    IndexUnavailable,
    RetrievalMode,
    RetrievalSetup,
    SearchFilters,
    collection_name,
    config_hash,
    parse_filters,
    point_count,
    retrieve,
)
from capitalcite.retrieval.types import Hit
from capitalcite.trace import ObservationKind, ask_attrs, git_sha, observation

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
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


@router.post("/ask", response_model=AskResponse)
async def post_ask(
    body: AskRequest,
    setup: Annotated[RetrievalSetup, Depends(get_setup)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AskResponse:
    """Return a grounded answer or a refusal. Both are HTTP 200."""
    with observation(
        "ask",
        ObservationKind.ASK,
        **ask_attrs(path=body.path, question=body.question),
    ) as span:
        try:
            count = await point_count(setup)
            if body.path == "agent":
                result = await _run_agent(body, setup, settings)
                response = _flatten(
                    result.answer,
                    setup,
                    count,
                    path="agent",
                    plan=list(result.plan),
                    traces=list(result.traces),
                    steps_used=result.steps_used,
                    tokens_used=result.tokens_used,
                )
            else:
                filters = parse_filters(ticker=body.ticker, form=body.form, period=body.period)
                limit = body.k if body.k is not None else setup.search_k
                hits = await retrieve(
                    setup, body.question, mode=RetrievalMode.HYBRID, k=limit, filters=filters
                )
                answer = await ask(
                    hits,
                    body.question,
                    _model_for(body, settings, hits_exist=bool(hits)),
                    min_score=settings.ask_min_hit_score,
                )
                response = _flatten(answer, setup, count)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (IndexUnavailable, IndexNotBuilt, ModelNotConfigured, ModelUnavailable) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        span.set(
            **ask_attrs(
                path=response.path,
                question=body.question,
                outcome=response.kind,
                refusal_reason=response.refusal_reason,
                config_hash=response.config_hash,
                git_sha=git_sha(),
            )
        )
        return response


async def _run_agent(body: AskRequest, setup: RetrievalSetup, settings: Settings) -> AgentResult:
    selected = body.model if body.model is not None else settings.ask_model
    model = build_agent_model(
        selected,
        api_key=settings.openai_api_key,
        openai_model=settings.ask_openai_model,
    )
    search_k = body.k if body.k is not None else setup.search_k

    async def search(
        query: str,
        *,
        k: int | None = None,
        filters: SearchFilters | None = None,
    ) -> list[Hit]:
        return await retrieve(
            setup,
            query,
            mode=RetrievalMode.HYBRID,
            k=k if k is not None else search_k,
            filters=filters,
        )

    sec_client = HttpxJsonClient(
        user_agent=settings.sec_user_agent,
        limiter=shared_limiter(),
    )
    fdic_client = HttpxJsonClient(
        user_agent=bankfind_user_agent(),
        timeout=BANKFIND_TIMEOUT,
    )
    if selected == "fake":
        xbrl = FakeXbrlClient().fact
        fdic = FakeFdicClient().lookup
    else:
        xbrl = XbrlClient(sec_client).fact
        fdic = FdicClient(fdic_client).lookup
    graph = open_store(settings)
    try:
        tools = AuditingTools(
            Gate(
                load_policy(settings.gate_policy),
                default_registry(search=search, xbrl=xbrl, fdic=fdic, graph=graph),
            ),
            AuditLog(settings.audit_log_path),
            request_trace_id(),
        )
        return await run_agent(
            body.question,
            model,
            tools,
            Budgets(max_steps=settings.ask_max_steps, max_tokens=settings.ask_max_tokens),
            min_score=settings.ask_min_hit_score,
        )
    finally:
        if graph is not None:
            await graph.close()


def _model_for(body: AskRequest, settings: Settings, *, hits_exist: bool) -> StructuredModel:
    """Empty hits never construct the OpenAI client, so a missing key stays a refusal."""
    if not hits_exist:
        return FakeStructuredModel()
    selected = body.model if body.model is not None else settings.ask_model
    return build_structured_model(
        selected,
        api_key=settings.openai_api_key,
        openai_model=settings.ask_openai_model,
    )


def _flatten(
    answer: Answer,
    setup: RetrievalSetup,
    count: int,
    *,
    path: AskPath = "rag",
    plan: list[PlanStep] | None = None,
    traces: list[TraceStep] | None = None,
    steps_used: int = 0,
    tokens_used: int = 0,
) -> AskResponse:
    digest = config_hash(setup.config)
    name = collection_name(setup.config)
    if isinstance(answer, Answered):
        return AskResponse(
            kind="answered",
            answer=answer.answer,
            citations=list(answer.citations),
            retrieval_score=answer.retrieval_score,
            refusal_reason=None,
            config_hash=digest,
            collection_name=name,
            point_count=count,
            path=path,
            plan=plan or [],
            traces=traces or [],
            steps_used=steps_used,
            tokens_used=tokens_used,
        )
    return AskResponse(
        kind="refused",
        answer=None,
        citations=[],
        retrieval_score=answer.retrieval_score,
        refusal_reason=answer.refusal_reason,
        config_hash=digest,
        collection_name=name,
        point_count=count,
        path=path,
        plan=plan or [],
        traces=traces or [],
        steps_used=steps_used,
        tokens_used=tokens_used,
    )
