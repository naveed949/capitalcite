from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.agent import (
    Budgets,
    FakeAgentModel,
    PlannerDecision,
    ToolCall,
    default_registry,
    run_agent,
)
from capitalcite.agent.calculator import calculate
from capitalcite.agent.tools import SearchFn, ToolRegistry
from capitalcite.api.app import create_app
from capitalcite.api.routes.ask import AskResponse
from capitalcite.api.routes.search import get_setup
from capitalcite.ask.types import Answered, DraftCite, ModelDraft, Refused
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
    if body.kind == "answered":
        assert body.citations
        assert body.refusal_reason is None


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


def test_ask_domain_still_avoids_agent_frameworks() -> None:
    banned = ("fastapi", "qdrant_client", "fastembed", "pydantic_ai", "langgraph")
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
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    application.dependency_overrides[get_settings] = lambda: _settings()
    return application, setup


def _settings() -> Settings:
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
