from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from qdrant_client import AsyncQdrantClient

from capitalcite.agent import FakeAgentModel, PlannerDecision, ToolCall
from capitalcite.api.app import create_app
from capitalcite.api.ask_models import AskResponse, ToolCallEvent, event_model_names
from capitalcite.api.routes.ask import AskRequest, iter_ask
from capitalcite.api.routes.search import get_setup
from capitalcite.audit import AuditRecord, verify
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import FakeEmbedder, RetrievalSetup, build_index, setup_retrieval

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"


def test_openapi_lists_stream_events() -> None:
    schema = create_app().openapi()
    stream = schema["paths"]["/ask/stream"]["post"]
    media = stream["responses"]["200"]["content"]
    assert "text/event-stream" in media
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


async def test_rag_event_order_and_answered_matches_ask(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    body = {
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


async def test_rag_refusal_has_no_citations(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(
        tmp_path, [_chunk(ticker="WFC", period="2025-12-31", text="Wells Fargo CET1 was 11.1%")]
    )
    body = {
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
    body = {
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
    assert denied["trace"]["ok"] is False
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
    application, setup = await _indexed_app(
        tmp_path, [_chunk(), _chunk(ticker="BAC", url=_BAC_URL)]
    )
    events = iter_ask(
        AskRequest.model_validate(
            {"question": "compare JPM and BAC", "path": "agent", "model": "fake"}
        ),
        setup,
        _settings(tmp_path),
    )
    saw_tool_call = False
    try:
        async for event in events:
            if isinstance(event, ToolCallEvent):
                saw_tool_call = True
                break
    finally:
        await events.aclose()
    for _ in range(40):
        if cancelled == 1:
            break
        await asyncio.sleep(0.05)
    assert saw_tool_call
    assert started == 1
    assert finished == 0
    assert cancelled == 1
    assert started < 2


async def test_blank_question_is_422_before_the_stream(tmp_path: Path) -> None:
    application, _setup = await _indexed_app(tmp_path, [_chunk()])
    transport = ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/ask/stream", json={"question": "   "})
    assert response.status_code == 422
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_ask_request_still_imports_from_the_route() -> None:
    assert AskRequest.model_fields["path"].default == "rag"


async def _post_json(application: FastAPI, path: str, body: dict[str, object]) -> dict[str, object]:
    transport = ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path, json=body)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


async def _read_stream(
    application: FastAPI, body: dict[str, object]
) -> tuple[int, str, list[dict[str, object]]]:
    transport = ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/ask/stream", json=body) as response:
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
