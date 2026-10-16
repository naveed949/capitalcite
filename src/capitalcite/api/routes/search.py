"""Ranked citation search. The process lifespan does not open Qdrant."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import (
    EmbedderNotConfigured,
    Hit,
    IndexNotBuilt,
    IndexUnavailable,
    RetrievalMode,
    RetrievalSetup,
    collection_name,
    config_hash,
    create_retrieval_setup,
    parse_filters,
    point_count,
    retrieve,
)

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    mode: RetrievalMode = RetrievalMode.HYBRID
    k: int | None = Field(default=None, ge=1, le=50)
    ticker: str | None = None
    form: str | None = None
    period: str | None = None


class SearchResponse(BaseModel):
    config_hash: str
    collection_name: str
    point_count: int = Field(ge=0)
    hits: list[Hit]


async def get_setup(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[RetrievalSetup]:
    """Build a setup for this request. Lifespan does not call this."""
    try:
        setup = create_retrieval_setup(settings)
    except EmbedderNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield setup
    finally:
        await setup.aclose()


@router.post("/search", response_model=SearchResponse)
async def post_search(
    body: SearchRequest,
    setup: Annotated[RetrievalSetup, Depends(get_setup)],
) -> SearchResponse:
    """Return ranked chunks. A missing collection or a failed RPC is 503."""
    try:
        filters = parse_filters(ticker=body.ticker, form=body.form, period=body.period)
        limit = body.k if body.k is not None else setup.search_k
        count = await point_count(setup)
        hits = await retrieve(setup, body.query, mode=body.mode, k=limit, filters=filters)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (IndexUnavailable, IndexNotBuilt) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SearchResponse(
        config_hash=config_hash(setup.config),
        collection_name=collection_name(setup.config),
        point_count=count,
        hits=hits,
    )
