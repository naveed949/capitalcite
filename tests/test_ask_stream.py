from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from httpx import ASGITransport
from qdrant_client import AsyncQdrantClient

from capitalcite.agent import FakeAgentModel, PlannerDecision, PlanStep, ToolCall
from capitalcite.agent.observe import Observe
from capitalcite.api.app import create_app
from capitalcite.api.ask_models import AskResponse, event_model_names, hoist_openapi_defs
from capitalcite.api.routes.ask import AskRequest, _note_client_cancel, _rag_events
from capitalcite.api.routes.search import get_setup
from capitalcite.ask.types import Refused
from capitalcite.audit import AuditRecord, verify
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import FakeEmbedder, RetrievalSetup, build_index, setup_retrieval
from capitalcite.retrieval.types import IndexConfig

_ORIGINAL_ASYNC_HTTP = httpx.AsyncHTTPTransport.handle_async_request
_ORIGINAL_SYNC_HTTP = httpx.HTTPTransport.handle_request

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"
_LEAK = "upstream https://internal.example/secret-path?token=abc123 exploded"


def test_openapi_lists_stream_events() -> None:
    schema = create_app().openapi()
    stream = schema["paths"]["/ask/stream"]["post"]
    media = stream["responses"]["200"]["content"]
    assert list(media) == ["text/event-stream"]
    dumped = json.dumps(media["text/event-stream"])
    names = event_model_names()
    assert names == (
        "plan",
        "tool_call",
        "tool_result",
        "tool_denied",
        "citation",
        "answer_delta",
        "answered",
        "refused",
        "error",
        "done",
    )
    for name in names:
        assert name in dumped


def test_openapi_refs_resolve() -> None:
    schema = create_app().openapi()
    _assert_refs_resolve(schema)


def test_hoist_rejects_conflicting_schema() -> None:
    with pytest.raises(ValueError, match="openapi schema clash: PlanEvent"):
        hoist_openapi_defs(
            {
                "components": {"schemas": {"PlanEvent": {"type": "string"}}},
                "$defs": {"PlanEvent": {"type": "object"}},
            }
        )


async def test_rag_event_order_and_answered_matches_ask(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    body: dict[str, object] = {
        "question": "What buffer did JPM describe?",
        "ticker": "JPM",
        "model": "fake",
    }
    ask = await _post_json(application, "/ask", body)
    status, media, events = await _read_stream(application, body)
    assert status == 200
    assert media.startswith("text/event-stream")
    names = [event["event"] for event in events]
    assert names[0] == "citation"
    assert "answer_delta" in names
    assert names[-2] == "answered"
    assert names[-1] == "done"
    assert "plan" not in names
    assert "tool_call" not in names
    answered = next(event for event in events if event["event"] == "answered")
    assert answered["response"] == ask
    AskResponse.model_validate(answered["response"])


async def test_agent_answered_matches_ask(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    body: dict[str, object] = {
        "question": "What buffer did JPM describe?",
        "path": "agent",
        "model": "fake",
    }
    ask = await _post_json(application, "/ask", body)
    status, media, events = await _read_stream(application, body)
    assert status == 200
    assert media.startswith("text/event-stream")
    names = [event["event"] for event in events]
    assert names[0] == "plan"
    assert "tool_call" in names
    assert names[-1] == "done"
    terminal = next(event for event in events if event["event"] in {"answered", "refused"})
    assert terminal["response"] == ask
    AskResponse.model_validate(terminal["response"])


async def test_rag_refusal_has_no_citations(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(
        tmp_path, [_chunk(ticker="WFC", period="2025-12-31", text="Wells Fargo CET1 was 11.1%")]
    )
    body: dict[str, object] = {
        "question": "What CET1 ratio did Wells Fargo disclose for year-end 2026?",
        "ticker": "WFC",
        "model": "fake",
    }
    ask = await _post_json(application, "/ask", body)
    _status, _media, events = await _read_stream(application, body)
    names = [event["event"] for event in events]
    assert names == ["refused", "done"]
    refused = events[0]["response"]
    assert refused == ask
    assert refused["kind"] == "refused"


async def test_gate_deny_emits_tool_denied_and_audit_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "capitalcite.api.routes.ask.build_agent_model",
        lambda *_args, **_kwargs: FakeAgentModel(
            plan_script=PlannerDecision(
                kind="plan",
                calls=(ToolCall(name="shell", args={"cmd": "ls"}),),
            )
        ),
    )
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    body: dict[str, object] = {
        "question": "What buffer did JPM describe?",
        "path": "agent",
        "model": "fake",
    }
    _status, _media, events = await _read_stream(application, body)
    names = [event["event"] for event in events]
    assert names[0] == "plan"
    assert "tool_call" in names
    assert "tool_denied" in names
    denied = next(event for event in events if event["event"] == "tool_denied")
    assert denied["reason"] == "unknown_tool"
    trace = denied["trace"]
    assert isinstance(trace, dict)
    assert trace["ok"] is False
    assert names[-1] == "done"
    report = verify(tmp_path / "audit.jsonl")
    assert report.ok is True
    assert report.records >= 1
    rows = [
        AuditRecord.model_validate_json(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row.decision == "denied" and row.tool == "shell" for row in rows)


async def test_disconnect_cancels_the_in_flight_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = 0
    finished = 0
    cancelled = 0

    async def slow_retrieve(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal started, finished, cancelled
        started += 1
        try:
            await asyncio.sleep(30)
            finished += 1
            return []
        except asyncio.CancelledError:
            cancelled += 1
            raise

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _ORIGINAL_ASYNC_HTTP)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _ORIGINAL_SYNC_HTTP)
    monkeypatch.setattr("capitalcite.api.routes.ask.retrieve", slow_retrieve)
    monkeypatch.setattr(
        "capitalcite.api.routes.ask.build_agent_model",
        lambda *_args, **_kwargs: FakeAgentModel(
            plan_script=PlannerDecision(
                kind="plan",
                calls=(
                    ToolCall(name="search_filings", args={"query": "one", "ticker": "JPM"}),
                    ToolCall(name="search_filings", args={"query": "two", "ticker": "BAC"}),
                ),
            )
        ),
    )
    application, _setup = await _indexed_app(
        tmp_path, [_chunk(), _chunk(ticker="BAC", url=_BAC_URL)]
    )
    server, serve_task, port = await _start_uvicorn(application)
    try:
        async with (
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client,
            client.stream(
                "POST",
                "/ask/stream",
                json={"question": "compare JPM and BAC", "path": "agent", "model": "fake"},
            ) as response,
        ):
            buf = ""
            async for chunk in response.aiter_text():
                buf += chunk
                if "event: tool_call" in buf or '"event":"tool_call"' in buf:
                    break
        for _ in range(40):
            if cancelled == 1:
                break
            await asyncio.sleep(0.05)
        assert started == 1
        assert finished == 0
        assert cancelled == 1
        assert started < 2
        report = verify(tmp_path / "audit.jsonl")
        assert report.ok is True
        assert report.records >= 1
    finally:
        server.should_exit = True
        await serve_task


async def test_disconnect_cancels_rag_retrieve_before_first_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started = 0
    finished = 0
    cancelled = 0

    async def slow_retrieve(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal started, finished, cancelled
        started += 1
        try:
            await asyncio.sleep(30)
            finished += 1
            return []
        except asyncio.CancelledError:
            cancelled += 1
            raise

    marker = tmp_path / "cancel.marker"
    monkeypatch.setenv("CAPITALCITE_CANCEL_MARKER", str(marker))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _ORIGINAL_ASYNC_HTTP)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _ORIGINAL_SYNC_HTTP)
    monkeypatch.setattr("capitalcite.api.routes.ask.retrieve", slow_retrieve)
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    server, serve_task, port = await _start_uvicorn(application)
    ask_logger = logging.getLogger("capitalcite.api.routes.ask")
    ask_logger.setLevel(logging.INFO)
    ask_logger.addHandler(caplog.handler)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5.0) as client:

            async def pending() -> None:
                async with client.stream(
                    "POST",
                    "/ask/stream",
                    json={
                        "question": "What buffer did JPM describe?",
                        "ticker": "JPM",
                        "model": "fake",
                    },
                ) as response:
                    async for _chunk in response.aiter_text():
                        pass

            task = asyncio.create_task(pending())
            for _ in range(40):
                if started == 1:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
                await task
        for _ in range(40):
            if cancelled == 1:
                break
            await asyncio.sleep(0.05)
        assert started == 1
        assert finished == 0
        assert cancelled == 1
        assert "ask stream cancelled by client" in caplog.text
        assert marker.read_text(encoding="utf-8") == "cancelled\n"
    finally:
        ask_logger.removeHandler(caplog.handler)
        server.should_exit = True
        await serve_task


def test_note_client_cancel_skips_marker_in_prod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "cancel.marker"
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CAPITALCITE_CANCEL_MARKER", str(marker))
    get_settings.cache_clear()
    try:
        _note_client_cancel()
        assert not marker.exists()
    finally:
        get_settings.cache_clear()


async def test_slow_retrieve_sleeps_only_for_the_cancel_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        order.append(f"sleep:{seconds}")

    async def fake_retrieve(_setup: object, query: str, **_kwargs: object) -> list[object]:
        order.append(f"retrieve:{query}")
        return []

    async def fake_ask(*_args: object, **_kwargs: object) -> Refused:
        return Refused(refusal_reason="no passages were retrieved")

    monkeypatch.setattr("capitalcite.api.routes.ask.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("capitalcite.api.routes.ask.retrieve", fake_retrieve)
    monkeypatch.setattr("capitalcite.api.routes.ask.ask", fake_ask)
    config = IndexConfig(
        embedder_id="bge-small",
        dense_size=384,
        sparse_id="bm25",
        chunker=ChunkerName.SECTION,
        chunk_size=512,
        chunk_overlap=64,
        collection_prefix="filings",
    )
    setup = cast(RetrievalSetup, SimpleNamespace(config=config, search_k=8))
    hold = "Hold this retrieve so cancel can land"

    async def run(question: str, ms: int) -> None:
        order.clear()
        settings = Settings.model_validate(
            {
                "sec_user_agent": "capitalcite-tests tests@example.com",
                "app_env": "test",
                "CAPITALCITE_TEST_SLOW_RETRIEVE_MS": ms,
            }
        )
        events = [
            event async for event in _rag_events(AskRequest(question=question), setup, settings, 0)
        ]
        assert events[0].event == "refused"

    await run(hold, 0)
    assert order == [f"retrieve:{hold}"]
    await run("What CET1 ratio did JPMorgan disclose?", 4000)
    assert order == ["retrieve:What CET1 ratio did JPMorgan disclose?"]
    await run(hold, 4000)
    assert order == [f"sleep:{4.0}", f"retrieve:{hold}"]


async def test_unexpected_error_after_200_emits_error_and_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(
        body: AskRequest,
        setup: object,
        settings: Settings,
        *,
        observe: Observe | None = None,
    ) -> None:
        _ = body, setup, settings
        step = PlanStep(tool="search_filings", args={"query": "one"}, status="pending")
        if observe is not None:
            observe.plan([step])
            observe.tool_call(step)
        raise RuntimeError(_LEAK)

    monkeypatch.setattr("capitalcite.api.routes.ask._run_agent", boom)
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    caplog.set_level(logging.ERROR, logger="capitalcite.api.routes.ask")
    status, media, events = await _read_stream(
        application,
        {"question": "What buffer did JPM describe?", "path": "agent", "model": "fake"},
    )
    assert status == 200
    assert media.startswith("text/event-stream")
    names = [event["event"] for event in events]
    assert names[0] == "plan"
    assert "error" in names
    assert names[-1] == "done"
    err = next(event for event in events if event["event"] == "error")
    assert err["status"] == 500
    assert err["detail"] == "internal error"
    dumped = json.dumps(events)
    assert "exploded" not in dumped
    assert "internal.example" not in dumped
    assert "abc123" not in dumped
    assert "ask stream failed" in caplog.text
    assert _LEAK in caplog.text


async def test_ask_unexpected_error_is_generic_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(
        body: AskRequest,
        setup: object,
        settings: Settings,
        *,
        observe: Observe | None = None,
    ) -> None:
        _ = body, setup, settings, observe
        raise RuntimeError(_LEAK)

    monkeypatch.setattr("capitalcite.api.routes.ask._run_agent", boom)
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/ask",
            json={"question": "What buffer did JPM describe?", "path": "agent", "model": "fake"},
        )
    assert response.status_code == 500
    body = response.text
    assert "Internal Server Error" in body
    assert "exploded" not in body
    assert "internal.example" not in body
    assert "abc123" not in body
    assert "application/json" not in response.headers.get("content-type", "")


async def test_blank_question_is_422_before_the_stream(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    transport = ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask/stream", json={"question": "   "})
    assert response.status_code == 422
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_ask_request_still_imports_from_the_route() -> None:
    assert AskRequest.model_fields["path"].default == "rag"


def _assert_refs_resolve(schema: Mapping[str, object]) -> None:
    def resolve(ref: str) -> object:
        assert ref.startswith("#/"), ref
        node: object = schema
        for part in ref[2:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            assert isinstance(node, dict), ref
            assert key in node, ref
            node = node[key]
        return node

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                resolve(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


async def _start_uvicorn(
    application: FastAPI,
) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    config = uvicorn.Config(
        application,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            return server, task, port
        if task.done():
            task.result()
            raise RuntimeError("uvicorn exited before listen")
        await asyncio.sleep(0.05)
    server.should_exit = True
    await task
    raise RuntimeError("uvicorn did not start")


async def _post_json(
    application: FastAPI, path: str, body: Mapping[str, object]
) -> dict[str, object]:
    transport = ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


async def _read_stream(
    application: FastAPI, body: Mapping[str, object]
) -> tuple[int, str, list[dict[str, object]]]:
    transport = ASGITransport(app=application)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        client.stream("POST", "/ask/stream", json=body) as response,
    ):
        text = "".join([chunk async for chunk in response.aiter_text()])
        media = response.headers.get("content-type", "")
        return response.status_code, media, _parse_sse(text)


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    name: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            assert isinstance(payload, dict)
            if name is not None:
                assert payload["event"] == name
            events.append(payload)
            name = None
    return events


async def _indexed_app(tmp_path: Path, chunks: list[Chunk]) -> tuple[FastAPI, RetrievalSetup]:
    _write_chunks(tmp_path, chunks)
    client = AsyncQdrantClient(":memory:")
    settings = _settings(tmp_path)
    setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
    await build_index(setup, tmp_path)
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: settings
    return application, setup


def _settings(tmp_path: Path) -> Settings:
    return Settings.model_validate(
        {
            "sec_user_agent": "capitalcite-tests tests@example.com",
            "chunker": ChunkerName.SECTION,
            "chunk_size": 32,
            "chunk_overlap": 4,
            "qdrant_collection": "filings",
            "embedder": "bge-small",
            "search_k": 8,
            "rrf_k": 60,
            "openai_api_key": None,
            "ask_model": "fake",
            "ask_openai_model": "gpt-4o-mini",
            "ask_min_hit_score": 0.0,
            "ask_max_steps": 8,
            "ask_max_tokens": 8000,
            "audit_log_path": tmp_path / "audit.jsonl",
        }
    )


def _chunk(
    *,
    ticker: str = "JPM",
    text: str = "tier one capital buffer",
    doc_id: str = "doc-jpm",
    url: str = _JPM_URL,
    period: str = "2024-12-31",
) -> Chunk:
    return Chunk(
        doc_id=doc_id,
        section="Item 7",
        start=10,
        end=10 + max(len(text), 20),
        offset=10,
        source_url=url,
        text=text,
        token_count=8,
        chunker=ChunkerName.SECTION,
        ticker=ticker,
        form="10-K",
        period=period,
        accession=doc_id,
    )


def _write_chunks(root: Path, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        folder = (
            root / "parsed" / "edgar" / chunk.ticker / chunk.form / chunk.period / chunk.accession
        )
        folder.mkdir(parents=True, exist_ok=True)
        existing = folder / "chunks.jsonl"
        line = chunk.model_dump_json()
        if existing.exists():
            prior = existing.read_text(encoding="utf-8")
            existing.write_text(prior + line + "\n", encoding="utf-8")
        else:
            existing.write_text(line + "\n", encoding="utf-8")
