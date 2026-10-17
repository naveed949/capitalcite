from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import ValidationError

from capitalcite.agent.tools import SearchFn, ToolFn
from capitalcite.agent.types import ToolResult
from capitalcite.ask.models import FakeStructuredModel, StructuredModel, build_structured_model
from capitalcite.ask.service import ask
from capitalcite.ask.types import Answer, Answered, AskModelName, Refused
from capitalcite.config import Settings
from capitalcite.mcp.types import AskToolArgs
from capitalcite.retrieval import (
    RetrievalError,
    RetrievalSetup,
    collection_name,
    config_hash,
    parse_filters,
    point_count,
)
from capitalcite.retrieval.types import Hit


def make_ask_runner(
    search: SearchFn,
    setup: RetrievalSetup,
    settings: Settings,
) -> ToolFn:
    async def ask_tool(args: Mapping[str, object]) -> ToolResult:
        try:
            parsed = AskToolArgs.model_validate(args)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                tool="ask",
                args=dict(args),
                detail=str(exc),
            )
        try:
            filters = parse_filters(ticker=parsed.ticker, form=parsed.form, period=parsed.period)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                tool="ask",
                args=dict(args),
                detail=str(exc),
            )
        try:
            hits = await search(parsed.question, k=parsed.k, filters=filters)
            count = await point_count(setup)
        except RetrievalError as exc:
            return ToolResult(
                ok=False,
                tool="ask",
                args=dict(args),
                detail=str(exc),
            )
        try:
            model = _model_for(parsed.model, settings, hits_exist=bool(hits))
            answer = await ask(
                hits,
                parsed.question,
                model,
                min_score=settings.ask_min_hit_score,
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool="ask",
                args=dict(args),
                detail=str(exc) or type(exc).__name__,
            )
        payload = _answer_payload(answer, setup, count)
        return ToolResult(
            ok=True,
            tool="ask",
            args=dict(args),
            detail=json.dumps(payload, sort_keys=True, default=str),
        )

    return ask_tool


def _model_for(
    requested: AskModelName | None,
    settings: Settings,
    *,
    hits_exist: bool,
) -> StructuredModel:
    if not hits_exist:
        return FakeStructuredModel()
    selected = requested if requested is not None else settings.ask_model
    return build_structured_model(
        selected,
        api_key=settings.openai_api_key,
        openai_model=settings.ask_openai_model,
    )


def _answer_payload(answer: Answer, setup: RetrievalSetup, count: int) -> dict[str, object]:
    digest = config_hash(setup.config)
    name = collection_name(setup.config)
    if isinstance(answer, Answered):
        return {
            "ok": True,
            "kind": "answered",
            "answer": answer.answer,
            "citations": [cite.model_dump(mode="json") for cite in answer.citations],
            "retrieval_score": answer.retrieval_score,
            "refusal_reason": None,
            "config_hash": digest,
            "collection_name": name,
            "point_count": count,
        }
    if isinstance(answer, Refused):
        return {
            "ok": True,
            "kind": "refused",
            "answer": None,
            "citations": [],
            "retrieval_score": answer.retrieval_score,
            "refusal_reason": answer.refusal_reason,
            "config_hash": digest,
            "collection_name": name,
            "point_count": count,
        }
    raise TypeError(f"unexpected answer type {type(answer)!r}")


def hits_payload(hits: tuple[Hit, ...]) -> list[dict[str, object]]:
    return [hit.model_dump(mode="json") for hit in hits]
