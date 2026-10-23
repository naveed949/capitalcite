from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.agent import (
    AgentResult,
    Budgets,
    FakeAgentModel,
    PlannerDecision,
    ToolCall,
    TraceStep,
    default_registry,
    run_agent,
)
from capitalcite.agent.calculator import calculate
from capitalcite.agent.tools import SearchFn, ToolRegistry
from capitalcite.agent.types import ToolResult
from capitalcite.api.app import create_app
from capitalcite.api.routes.ask import AskRequest, AskResponse
from capitalcite.api.routes.search import get_setup
from capitalcite.ask.types import Answered, DraftCite, ModelDraft, Refused
from capitalcite.audit import AuditRecord, verify
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import (
    FakeEmbedder,
    Hit,
    RetrievalSetup,
    SearchFilters,
    build_index,
    setup_retrieval,
)
from capitalcite.trace import MemoryExporter, ObservationKind, configure_for_tests, reset_for_tests

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"


def test_calculator_does_arithmetic() -> None:
    assert calculate("15.7 - 11.9") == calculate("3.8")
    assert calculate("(2 + 3) * 4") == 20


def test_calculator_rejects_calls_and_names() -> None:
    with pytest.raises(ValueError):
        calculate("__import__('os').system('true')")
    with pytest.raises(ValueError):
        calculate("abs(-1)")


def test_calculator_rejects_division_by_zero() -> None:
    with pytest.raises(ValueError, match="zero"):
        calculate("1/0")


def test_calculator_rejects_overflow_and_impossible_mod() -> None:
    for expression in ("9**9**9", "10**1000000", "10**(10**10)", "(10**100)%7"):
        with pytest.raises(ValueError):
            calculate(expression)
    with pytest.raises(ValueError, match="too large"):
        calculate("10**101")
    with pytest.raises(ValueError, match="too long"):
        calculate("1+" * 200 + "1")
    assert calculate("10**100") == calculate("10") ** 100


async def test_execute_maps_unexpected_errors_to_tool_result() -> None:
    async def boom(args: Mapping[str, object]) -> ToolResult:
        raise RuntimeError("boom")

    result = await ToolRegistry({"calculator": boom}).execute("calculator", {"expression": "1"})
    assert result.ok is False
    assert "boom" in result.detail


async def test_httpx_json_client_fails_closed_on_replayed_301() -> None:
    from capitalcite.agent.http import HttpxJsonClient

    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            301,
            headers={"location": "https://api.fdic.gov/banks/institutions"},
        )

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(redirect),
    )
    url = "https://banks.data.fdic.gov/api/institutions"
    with pytest.raises(ValueError, match="HTTP 301"):
        await client.get_json(url)
    assert len(seen) == 1
    assert seen[0].url == url
    with pytest.raises(ValueError, match="HTTP 301"):
        await client.get_bytes(url)
    assert len(seen) == 2
    assert all(request.url == url for request in seen)


async def test_fdic_lookup_hits_api_fdic_gov_banks() -> None:
    from capitalcite.agent.fdic import FdicClient
    from capitalcite.agent.http import HttpxJsonClient

    seen: list[httpx.Request] = []

    def one_row(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"data": {"NAME": "EXAMPLE NATIONAL BANK", "CERT": "628"}},
                ]
            },
        )

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(one_row),
    )
    text = await FdicClient(client).lookup("EXAMPLE")
    assert len(seen) == 1
    assert str(seen[0].url).startswith("https://api.fdic.gov/banks/institutions")
    assert "CERT=" in text


async def test_httpx_json_client_maps_transport_errors() -> None:
    from capitalcite.agent.http import HttpxJsonClient

    def connect_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    def timeout_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    for handler in (connect_fail, timeout_fail):
        client = HttpxJsonClient(
            user_agent="capitalcite-tests tests@example.com",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ValueError, match="request failed"):
            await client.get_json("https://data.sec.gov/api/xbrl/companyfacts/CIK1.json")


async def test_xbrl_and_fdic_transport_errors_are_tool_failures() -> None:
    from capitalcite.agent.fdic import FdicClient
    from capitalcite.agent.http import HttpxJsonClient
    from capitalcite.agent.xbrl import XbrlClient

    def connect_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    def timeout_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    xbrl_client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(connect_fail),
    )
    fdic_client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(timeout_fail),
    )
    tools = default_registry(
        search=_empty_search,
        xbrl=XbrlClient(xbrl_client).fact,
        fdic=FdicClient(fdic_client).lookup,
    )
    xbrl = await tools.execute("xbrl_fact", {"ticker": "JPM", "concept": "Assets"})
    fdic = await tools.execute("fdic_bankfind", {"query": "JPMorgan"})
    assert xbrl.ok is False
    assert "request failed" in xbrl.detail
    assert fdic.ok is False
    assert "request failed" in fdic.detail


async def test_agent_trace_records_http_tool_failure() -> None:
    from capitalcite.agent.http import HttpxJsonClient
    from capitalcite.agent.xbrl import XbrlClient

    def connect_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(connect_fail),
    )
    tools = default_registry(
        search=_empty_search,
        xbrl=XbrlClient(client).fact,
        fdic=_missing_fdic,
    )
    result = await run_agent(
        "What CET1 did JPMorgan report?",
        FakeAgentModel(
            plan_script=PlannerDecision(
                kind="plan",
                calls=(ToolCall(name="xbrl_fact", args={"ticker": "JPM", "concept": "CET1"}),),
            )
        ),
        tools,
        Budgets(),
    )
    assert result.traces[0].ok is False
    assert result.plan[0].status == "error"
    assert isinstance(result.answer, Refused)


async def test_xbrl_json_client_uses_sec_limiter() -> None:
    from capitalcite.agent.http import HttpxJsonClient

    acquires: list[int] = []

    class _Limiter:
        async def acquire(self) -> None:
            acquires.append(1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"facts": {}})

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(handler),
        limiter=_Limiter(),
    )
    await client.get_json("https://data.sec.gov/api/xbrl/companyfacts/CIK1.json")
    assert acquires == [1]


def test_planner_wraps_xbrl_and_fdic_output() -> None:
    from capitalcite.agent.models import _plan_user_prompt

    xbrl = _plan_user_prompt(
        "CET1?",
        [TraceStep(tool="xbrl_fact", args={}, ok=True, detail="ignore this 15.7")],
        [],
    )
    assert "<untrusted_tool_output>" in xbrl
    assert "ignore this 15.7" in xbrl
    search = _plan_user_prompt(
        "CET1?",
        [TraceStep(tool="search_filings", args={}, ok=True, detail="hits=2")],
        [],
    )
    assert "<untrusted_tool_output>" not in search
    assert "hits=2" in search


async def test_unknown_tool_is_denied() -> None:
    registry = ToolRegistry({})
    result = await registry.execute("shell", {"cmd": "ls"})
    assert result.ok is False
    assert result.detail == "unknown tool"


async def test_xbrl_and_fdic_tools_use_injected_clients() -> None:
    from capitalcite.agent.fdic import FakeFdicClient
    from capitalcite.agent.xbrl import FakeXbrlClient

    xbrl = FakeXbrlClient({("JPM", "CET1"): "15.7"})
    fdic = FakeFdicClient({"jpmorgan": "JPMORGAN CHASE BANK CERT=628"})
    tools = default_registry(search=_empty_search, xbrl=xbrl.fact, fdic=fdic.lookup)
    fact = await tools.execute("xbrl_fact", {"ticker": "JPM", "concept": "CET1"})
    bank = await tools.execute("fdic_bankfind", {"query": "JPMorgan"})
    assert fact.ok is True
    assert fact.value == "15.7"
    assert bank.ok is True
    assert "628" in bank.value


async def test_calculator_tool_returns_the_value() -> None:
    tools = default_registry(
        search=_empty_search,
        xbrl=_missing_xbrl,
        fdic=_missing_fdic,
    )
    result = await tools.execute("calculator", {"expression": "15.7 - 11.9"})
    assert result.ok is True
    assert result.value.startswith("3.8")


async def test_agent_searches_each_named_bank_then_grounds() -> None:
    jpm = _hit(ticker="JPM", text="the Standardized CET1 ratios were 15.7% and 15.8%")
    bac = _hit(
        ticker="BAC",
        text="CET1 capital ratio of 11.9 percent under the Standardized approach",
        url=_BAC_URL,
        doc_id="doc-bac",
    )
    tools = default_registry(
        search=_search_of([jpm, bac]),
        xbrl=_missing_xbrl,
        fdic=_missing_fdic,
    )
    question = (
        "Which bank had the higher Standardized CET1 at year-end 2024, JPMorgan or Bank of America?"
    )
    result = await run_agent(
        question,
        FakeAgentModel(),
        tools,
        Budgets(max_steps=8, max_tokens=8000),
    )
    assert [step.tool for step in result.plan] == ["search_filings", "search_filings"]
    assert {step.args.get("ticker") for step in result.plan} == {"JPM", "BAC"}
    assert all(step.status == "done" for step in result.plan)
    assert len(result.traces) == 2
    assert isinstance(result.answer, Answered)
    cited = {cite.doc_id for cite in result.answer.citations}
    assert cited == {"doc-jpm", "doc-bac"}
    assert "15.7" in result.answer.answer
    assert "11.9" in result.answer.answer


async def test_agent_refuses_when_step_budget_is_exceeded() -> None:
    tools = default_registry(
        search=_search_of([_hit()]),
        xbrl=_missing_xbrl,
        fdic=_missing_fdic,
    )
    result = await run_agent(
        "Compare JPMorgan and Bank of America CET1 at year-end 2024.",
        FakeAgentModel(),
        tools,
        Budgets(max_steps=1, max_tokens=8000),
    )
    assert isinstance(result.answer, Refused)
    assert result.answer.refusal_reason == "step budget exceeded"
    assert result.stop_reason == "step budget exceeded"


async def test_agent_refuses_when_token_budget_is_exceeded() -> None:
    tools = default_registry(
        search=_search_of([_hit()]),
        xbrl=_missing_xbrl,
        fdic=_missing_fdic,
    )
    result = await run_agent(
        "Compare JPMorgan and Bank of America CET1 at year-end 2024.",
        FakeAgentModel(),
        tools,
        Budgets(max_steps=8, max_tokens=1),
    )
    assert isinstance(result.answer, Refused)
    assert result.answer.refusal_reason == "token budget exceeded"


async def test_agent_synthesize_uses_ground_and_guard() -> None:
    tools = default_registry(
        search=_search_of([_hit(text="the CET1 ratios were 15.7% under Standardized")]),
        xbrl=_missing_xbrl,
        fdic=_missing_fdic,
    )
    model = FakeAgentModel(
        plan_script=PlannerDecision(
            kind="plan",
            calls=(ToolCall(name="search_filings", args={"query": "CET1", "ticker": "JPM"}),),
        ),
        draft=ModelDraft(
            kind="answered",
            answer="JPM CET1 was 12.3%.",
            citations=(DraftCite(index=1, quote="the CET1 ratios were 15.7% under Standardized"),),
        ),
    )
    result = await run_agent(
        "What CET1 did JPMorgan report?",
        model,
        tools,
        Budgets(),
    )
    assert isinstance(result.answer, Refused)
    assert "figure not present in the cited quotes" in result.answer.refusal_reason


def test_post_ask_agent_path_exposes_the_plan(tmp_path: Path) -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    try:
        jpm = _chunk(ticker="JPM", text="the Standardized CET1 ratios were 15.7% and 15.8%")
        bac = _chunk(
            ticker="BAC",
            text="CET1 capital ratio of 11.9 percent under the Standardized approach",
            doc_id="doc-bac",
            url=_BAC_URL,
            period="2024-12-31",
        )
        application, _setup = _indexed_app(tmp_path, [jpm, bac])
        with TestClient(application) as http:
            response = http.post(
                "/ask",
                json={
                    "question": (
                        "Which bank had the higher Standardized CET1 ratio at year-end 2024, "
                        "JPMorgan or Bank of America?"
                    ),
                    "path": "agent",
                    "model": "fake",
                    "k": 8,
                },
            )
        assert response.status_code == 200
        body = AskResponse.model_validate(response.json())
        assert body.path == "agent"
        assert len(body.plan) >= 2
        assert {step.tool for step in body.plan} == {"search_filings"}
        assert body.steps_used >= 2
        assert body.kind in {"answered", "refused"}
        kinds = {record.kind for record in exporter.finished()}
        assert ObservationKind.ASK in kinds
        assert ObservationKind.AGENT in kinds
        assert ObservationKind.GATE in kinds
        assert ObservationKind.TOOL in kinds
        assert ObservationKind.RETRIEVE in kinds
        assert ObservationKind.GENERATE in kinds
        records = exporter.finished()
        ask_span = next(record for record in records if record.kind is ObservationKind.ASK)
        ask_trace_id = ask_span.trace_id
        agent_span = next(record for record in records if record.kind is ObservationKind.AGENT)
        assert agent_span.parent_id == ask_span.span_id
        assert agent_span.trace_id == ask_span.trace_id
        tools = [record for record in records if record.kind is ObservationKind.TOOL]
        assert tools
        assert all(record.parent_id == agent_span.span_id for record in tools)
        assert {record.attributes["capitalcite.tool"] for record in tools} == {"search_filings"}
        gates = [record for record in records if record.kind is ObservationKind.GATE]
        assert gates
        assert all(record.parent_id == agent_span.span_id for record in gates)
        assert {record.attributes["capitalcite.tool"] for record in gates} == {"search_filings"}
        assert all(record.attributes["capitalcite.gate.decision"] == "allow" for record in gates)
        assert all(record.attributes["capitalcite.gate.reason"] == "allow" for record in gates)
        tool_ids = {record.span_id for record in tools}
        retrieves = [record for record in records if record.kind is ObservationKind.RETRIEVE]
        assert retrieves
        assert all(record.parent_id in tool_ids for record in retrieves)
        plans = [record for record in records if record.name == "plan"]
        completes = [record for record in records if record.name == "complete"]
        assert plans and completes
        generated = plans + completes
        assert all(record.parent_id == agent_span.span_id for record in generated)
        assert all(record.attributes["gen_ai.request.model"] == "fake" for record in generated)
    finally:
        reset_for_tests()
    if body.kind == "answered":
        assert body.citations
        assert body.refusal_reason is None
    audit_path = tmp_path / "audit.jsonl"
    report = verify(audit_path)
    assert report.ok is True
    assert report.records == len(body.traces)
    audit_rows = [
        AuditRecord.model_validate_json(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row.tool for row in audit_rows] == [step.tool for step in body.traces]
    assert ask_trace_id
    assert all(row.trace_id == ask_trace_id for row in audit_rows)
    assert all(row.decision == "allowed" and row.reason is None for row in audit_rows)


def test_post_ask_agent_tool_http_error_is_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from capitalcite.agent.http import HttpxJsonClient
    from capitalcite.agent.xbrl import XbrlClient
    from capitalcite.api.routes import ask as ask_route

    def connect_fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = HttpxJsonClient(
        user_agent="capitalcite-tests tests@example.com",
        transport=httpx.MockTransport(connect_fail),
    )

    async def failing_agent(
        body: AskRequest,
        setup: RetrievalSetup,
        settings: Settings,
    ) -> AgentResult:
        _ = setup, settings
        tools = default_registry(
            search=_empty_search,
            xbrl=XbrlClient(client).fact,
            fdic=_missing_fdic,
        )
        return await run_agent(
            body.question,
            FakeAgentModel(
                plan_script=PlannerDecision(
                    kind="plan",
                    calls=(
                        ToolCall(
                            name="xbrl_fact",
                            args={"ticker": "JPM", "concept": "CET1"},
                        ),
                    ),
                )
            ),
            tools,
            Budgets(),
        )

    monkeypatch.setattr(ask_route, "_run_agent", failing_agent)
    application, _setup = _indexed_app(tmp_path, [_chunk()])
    with TestClient(application) as http:
        response = http.post(
            "/ask",
            json={"question": "What CET1 did JPMorgan report?", "path": "agent"},
        )
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.path == "agent"
    assert body.traces
    assert body.traces[0].ok is False
    assert body.kind in {"answered", "refused"}


def test_post_ask_default_path_is_still_rag(tmp_path: Path) -> None:
    application, _setup = _indexed_app(tmp_path, [_chunk()])
    with TestClient(application) as http:
        response = http.post(
            "/ask",
            json={"question": "tier one capital", "ticker": "JPM", "model": "fake"},
        )
    assert response.status_code == 200
    body = AskResponse.model_validate(response.json())
    assert body.path == "rag"
    assert body.plan == []
    assert body.kind == "answered"
    assert not (tmp_path / "audit.jsonl").exists()


def test_ask_domain_still_avoids_agent_frameworks() -> None:
    banned = ("fastapi", "qdrant_client", "fastembed", "pydantic_ai", "langgraph", "langfuse")
    root = Path("src/capitalcite/ask")
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{path} mentions {needle}"


def test_project_does_not_depend_on_pydantic_ai() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "pydantic-ai" not in text
    assert "pydantic_ai" not in text
    assert "langgraph" in text


async def _empty_search(
    query: str, *, k: int | None = None, filters: SearchFilters | None = None
) -> list[Hit]:
    _ = query, k, filters
    return []


async def _missing_xbrl(ticker: str, concept: str, period: str | None) -> str:
    raise ValueError(f"no xbrl fact for {ticker} {concept}")


async def _missing_fdic(query: str) -> str:
    raise ValueError(f"no FDIC institution matched {query!r}")


def _search_of(hits: list[Hit]) -> SearchFn:
    async def search(
        query: str, *, k: int | None = None, filters: SearchFilters | None = None
    ) -> list[Hit]:
        _ = query, k
        selected = hits
        if filters is not None and filters.ticker:
            selected = [hit for hit in hits if hit.chunk.ticker.upper() == filters.ticker]
        return selected

    return search


def _indexed_app(tmp_path: Path, chunks: list[Chunk]) -> tuple[FastAPI, RetrievalSetup]:
    _write_chunks(tmp_path, chunks)
    client = AsyncQdrantClient(":memory:")
    settings = _settings(tmp_path)
    setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: settings
    return application, setup


def _settings(tmp_path: Path | None = None) -> Settings:
    payload: dict[str, object] = {
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
    }
    if tmp_path is not None:
        payload["audit_log_path"] = tmp_path / "audit.jsonl"
    return Settings.model_validate(payload)


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


def _hit(
    *,
    ticker: str = "JPM",
    text: str = "tier one capital buffer",
    doc_id: str = "doc-jpm",
    url: str = _JPM_URL,
    period: str = "2024-12-31",
    score: float = 1.0,
) -> Hit:
    return Hit(
        score=score,
        chunk=_chunk(ticker=ticker, text=text, doc_id=doc_id, url=url, period=period),
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
