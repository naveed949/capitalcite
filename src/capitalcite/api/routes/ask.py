"""Cited answers over retrieved chunks. The process lifespan does not open Qdrant."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

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
from capitalcite.agent.observe import Observe
from capitalcite.agent.xbrl import FakeXbrlClient, XbrlClient
from capitalcite.api.ask_limit import enforce_ask_limit
from capitalcite.api.ask_models import (
    AnsweredEvent,
    AskEvent,
    AskFault,
    AskRequest,
    AskResponse,
    DoneEvent,
    ErrorEvent,
    PlanEvent,
    RefusedEvent,
    ToolCallEvent,
    ask_event_openapi_schema,
    encode_sse,
    fault_detail,
    fold_response,
    terminal_facts,
    tool_fact,
)
from capitalcite.api.routes.search import get_setup
from capitalcite.ask.models import FakeStructuredModel, StructuredModel, build_structured_model
from capitalcite.ask.service import ask
from capitalcite.ask.types import (
    Answer,
    Answered,
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

router = APIRouter(tags=["ask"], dependencies=[Depends(enforce_ask_limit)])
logger = logging.getLogger(__name__)

__all__ = ["AskRequest", "AskResponse", "iter_ask", "router"]

_INTERNAL_ERROR = ErrorEvent(status=500, detail="internal error")

_SSE_START_HEADERS = [
    (b"cache-control", b"no-cache"),
    (b"content-type", b"text/event-stream"),
    (b"x-accel-buffering", b"no"),
]

_STREAM_FAULTS = (IndexUnavailable, IndexNotBuilt, ModelNotConfigured, ModelUnavailable)


class _QueueObserve(Observe):
    def __init__(self, queue: asyncio.Queue[AskEvent]) -> None:
        self._queue = queue

    def plan(self, steps: Sequence[PlanStep]) -> None:
        if steps:
            self._queue.put_nowait(PlanEvent(plan=list(steps)))

    def tool_call(self, step: PlanStep) -> None:
        self._queue.put_nowait(ToolCallEvent(step=step))

    def tool_finished(self, trace: TraceStep, reason: str | None) -> None:
        self._queue.put_nowait(tool_fact(trace, reason))


class _AskSSEResponse(Response):
    """Peek the first event in the same task that writes the body."""

    media_type = "text/event-stream"

    def __init__(self, events: AsyncGenerator[AskEvent, None]) -> None:
        super().__init__(content=b"", media_type="text/event-stream")
        self._events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        events = self._events
        try:
            first = await anext(events)
        except ValueError as exc:
            await events.aclose()
            await _send_json_error(send, 422, str(exc))
            return
        except _STREAM_FAULTS as exc:
            await events.aclose()
            await _send_json_error(send, 503, str(exc))
            return
        except Exception:
            await events.aclose()
            raise
        if isinstance(first, ErrorEvent):
            await events.aclose()
            await _send_json_error(send, first.status, first.detail)
            return

        await send({"type": "http.response.start", "status": 200, "headers": _SSE_START_HEADERS})
        current = asyncio.current_task()
        cancelled_by_client = False

        async def watch_disconnect() -> None:
            nonlocal cancelled_by_client
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    cancelled_by_client = True
                    if current is not None:
                        current.cancel()
                    return

        watcher = asyncio.create_task(watch_disconnect())
        try:
            await send(
                {
                    "type": "http.response.body",
                    "body": encode_sse(first),
                    "more_body": True,
                }
            )
            async for event in events:
                await send(
                    {
                        "type": "http.response.body",
                        "body": encode_sse(event),
                        "more_body": True,
                    }
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            if not cancelled_by_client:
                raise
            if current is not None:
                current.uncancel()
        except Exception:
            logger.exception("ask stream failed")
            await send(
                {
                    "type": "http.response.body",
                    "body": encode_sse(_INTERNAL_ERROR),
                    "more_body": True,
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": encode_sse(DoneEvent()),
                    "more_body": True,
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            await events.aclose()


async def _send_json_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


@router.post("/ask", response_model=AskResponse)
async def post_ask(
    body: AskRequest,
    setup: Annotated[RetrievalSetup, Depends(get_setup)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AskResponse:
    """Return a grounded answer or a refusal. Both are HTTP 200."""
    try:
        return await fold_response(iter_ask(body, setup, settings))
    except AskFault as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except _STREAM_FAULTS as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/ask/stream",
    response_model=None,
    responses={
        200: {
            "description": "Server-sent events for one ask run.",
            "content": {
                "text/event-stream": {
                    "schema": ask_event_openapi_schema(),
                }
            },
        }
    },
)
async def post_ask_stream(
    body: AskRequest,
    setup: Annotated[RetrievalSetup, Depends(get_setup)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> _AskSSEResponse:
    """Return the same run as ``POST /ask`` as ``text/event-stream``."""
    return _AskSSEResponse(iter_ask(body, setup, settings))


async def iter_ask(
    body: AskRequest,
    setup: RetrievalSetup,
    settings: Settings,
) -> AsyncGenerator[AskEvent, None]:
    """Yield one run, then exactly one ``done``."""
    with observation(
        "ask",
        ObservationKind.ASK,
        **ask_attrs(path=body.path, question=body.question),
    ) as span:
        fault: ErrorEvent | None = None
        response: AskResponse | None = None
        try:
            count = await point_count(setup)
            if body.path == "agent":
                async for event in _agent_events(body, setup, settings, count):
                    if isinstance(event, AnsweredEvent | RefusedEvent):
                        response = event.response
                    elif isinstance(event, ErrorEvent):
                        fault = event
                    yield event
            else:
                async for event in _rag_events(body, setup, settings, count):
                    if isinstance(event, AnsweredEvent | RefusedEvent):
                        response = event.response
                    elif isinstance(event, ErrorEvent):
                        fault = event
                    yield event
        except ValueError as exc:
            fault = ErrorEvent(status=422, detail=fault_detail(exc))
            yield fault
        except _STREAM_FAULTS as exc:
            fault = ErrorEvent(status=503, detail=fault_detail(exc))
            yield fault
        if response is not None:
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
        elif fault is not None:
            span.set(
                **ask_attrs(
                    path=body.path,
                    question=body.question,
                    outcome="error",
                    git_sha=git_sha(),
                )
            )
        yield DoneEvent()


async def _rag_events(
    body: AskRequest,
    setup: RetrievalSetup,
    settings: Settings,
    count: int,
) -> AsyncIterator[AskEvent]:
    filters = parse_filters(ticker=body.ticker, form=body.form, period=body.period)
    limit = body.k if body.k is not None else setup.search_k
    hits = await retrieve(setup, body.question, mode=RetrievalMode.HYBRID, k=limit, filters=filters)
    answer = await ask(
        hits,
        body.question,
        _model_for(body, settings, hits_exist=bool(hits)),
        min_score=settings.ask_min_hit_score,
    )
    for event in terminal_facts(_flatten(answer, setup, count)):
        yield event


async def _agent_events(
    body: AskRequest,
    setup: RetrievalSetup,
    settings: Settings,
    count: int,
) -> AsyncIterator[AskEvent]:
    queue: asyncio.Queue[AskEvent] = asyncio.Queue()
    task = asyncio.create_task(_run_agent(body, setup, settings, observe=_QueueObserve(queue)))
    getter: asyncio.Task[AskEvent] | None = None
    try:
        while True:
            if task.done() and queue.empty():
                break
            if task.done():
                yield queue.get_nowait()
                continue
            getter = asyncio.create_task(queue.get())
            _done, pending = await asyncio.wait(
                {getter, task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if getter in pending:
                getter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await getter
                getter = None
                continue
            event = getter.result()
            getter = None
            yield event
        result = await task
        for event in terminal_facts(
            _flatten(
                result.answer,
                setup,
                count,
                path="agent",
                plan=list(result.plan),
                traces=list(result.traces),
                steps_used=result.steps_used,
                tokens_used=result.tokens_used,
            )
        ):
            yield event
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await getter
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _run_agent(
    body: AskRequest,
    setup: RetrievalSetup,
    settings: Settings,
    *,
    observe: Observe | None = None,
) -> AgentResult:
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
            observe=observe,
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
