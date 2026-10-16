"""Cited answers over retrieved chunks. The process lifespan does not open Qdrant."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

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
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import (
    IndexNotBuilt,
    IndexUnavailable,
    RetrievalMode,
    RetrievalSetup,
    collection_name,
    config_hash,
    parse_filters,
    point_count,
    retrieve,
)

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    k: int | None = Field(default=None, ge=1, le=50)
    ticker: str | None = None
    form: str | None = None
    period: str | None = None
    model: AskModelName | None = None

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
    confidence: float
    refusal_reason: str | None = None
    config_hash: str
    collection_name: str
    point_count: int = Field(ge=0)

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
    try:
        filters = parse_filters(ticker=body.ticker, form=body.form, period=body.period)
        limit = body.k if body.k is not None else setup.search_k
        count = await point_count(setup)
        hits = await retrieve(
            setup, body.question, mode=RetrievalMode.HYBRID, k=limit, filters=filters
        )
        answer = await ask(hits, body.question, _model_for(body, settings, hits_exist=bool(hits)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (IndexUnavailable, IndexNotBuilt, ModelNotConfigured, ModelUnavailable) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _flatten(answer, setup, count)


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


def _flatten(answer: Answer, setup: RetrievalSetup, count: int) -> AskResponse:
    digest = config_hash(setup.config)
    name = collection_name(setup.config)
    if isinstance(answer, Answered):
        return AskResponse(
            kind="answered",
            answer=answer.answer,
            citations=list(answer.citations),
            confidence=answer.confidence,
            refusal_reason=None,
            config_hash=digest,
            collection_name=name,
            point_count=count,
        )
    return AskResponse(
        kind="refused",
        answer=None,
        citations=[],
        confidence=answer.confidence,
        refusal_reason=answer.refusal_reason,
        config_hash=digest,
        collection_name=name,
        point_count=count,
    )
