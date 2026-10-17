from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.agent.graph import run_agent
from capitalcite.agent.models import FakeAgentModel
from capitalcite.agent.tools import default_registry
from capitalcite.agent.types import Budgets
from capitalcite.cli import app
from capitalcite.config import Settings, get_settings
from capitalcite.eval.graph_eval import run_graph_suite
from capitalcite.kg import (
    GraphQuery,
    GraphQueryKind,
    MemoryGraphStore,
    load_snapshot,
    open_store,
)
from capitalcite.retrieval.types import Hit, SearchFilters

runner = CliRunner()
_SNAPSHOT = Path("evals/golden/graph_snapshot.json")


async def test_memory_load_is_idempotent() -> None:
    store = MemoryGraphStore()
    snapshot = load_snapshot(_SNAPSHOT)
    first = await store.load(snapshot)
    second = await store.load(snapshot)
    assert first.banks == second.banks == 8
    assert first.filings == second.filings
    assert first.metrics == second.metrics
    assert first.edges == second.edges == 4
    await store.close()


async def test_subsidiaries_of_jpm_include_chase_bank_rssd() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    result = await store.query(GraphQuery(kind=GraphQueryKind.SUBSIDIARIES, ticker="JPM"))
    assert result.ok is True
    assert "852218" in result.detail
    assert "JPMORGAN CHASE BANK" in result.detail


async def test_parent_of_citibank_is_citigroup() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    result = await store.query(GraphQuery(kind=GraphQueryKind.PARENT, rssd="476810"))
    assert result.ok is True
    assert "1951350" in result.detail
    assert "C" in result.detail


async def test_jpm_2024_filings_and_cet1_metric() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    filings = await store.query(
        GraphQuery(kind=GraphQueryKind.FILINGS, ticker="JPM", form="10-K", period="2024")
    )
    metric = await store.query(
        GraphQuery(kind=GraphQueryKind.METRIC, ticker="JPM", name="CET1", period="2024")
    )
    assert filings.ok is True
    assert "0000019617-25-000270" in filings.detail
    assert "0000019617-24-000225" not in filings.detail
    assert metric.ok is True
    assert "15.7" in metric.detail


async def test_path_from_jpm_to_chase_bank() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    result = await store.query(GraphQuery(kind=GraphQueryKind.PATH, ticker="JPM", rssd="852218"))
    assert result.ok is True
    assert "1039502" in result.detail
    assert "852218" in result.detail


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {"sec_user_agent": "capitalcite-tests tests@example.com"}
    payload.update(overrides)
    return Settings.model_validate(payload)


def test_blank_uri_opens_nothing() -> None:
    assert open_store(_settings()) is None
    assert open_store(_settings(neo4j_uri="  ")) is None


def test_open_store_with_uri_does_not_dial() -> None:
    store = open_store(_settings(neo4j_uri="bolt://127.0.0.1:1", neo4j_password="unused"))
    assert store is not None


async def test_unknown_bank_is_not_ok() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    result = await store.query(GraphQuery(kind=GraphQueryKind.SUBSIDIARIES, ticker="ZZZ"))
    assert result.ok is False
    assert "unknown bank" in result.detail


def test_cli_help_lists_graph() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "graph" in result.stdout


def test_blank_neo4j_uri_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("NEO4J_URI", "  ")
    settings = Settings()
    assert settings.neo4j_uri is None


async def test_graph_query_unknown_kind_is_invalid() -> None:
    tools = default_registry(search=_empty_search, xbrl=_missing_xbrl, fdic=_missing_fdic)
    result = await tools.execute("graph_query", {"kind": "cypher", "ticker": "JPM"})
    assert result.ok is False


async def test_graph_query_unconfigured_does_not_raise() -> None:
    tools = default_registry(search=_empty_search, xbrl=_missing_xbrl, fdic=_missing_fdic)
    result = await tools.execute("graph_query", {"kind": "subsidiaries", "ticker": "JPM"})
    assert result.ok is False
    assert "graph not configured" in result.detail


async def test_graph_query_memory_returns_chase_rssd() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    tools = default_registry(
        search=_empty_search, xbrl=_missing_xbrl, fdic=_missing_fdic, graph=store
    )
    result = await tools.execute("graph_query", {"kind": "subsidiaries", "ticker": "JPM"})
    assert result.ok is True
    assert "852218" in result.detail


async def test_fake_planner_skips_graph_on_cet1_comparison() -> None:
    model = FakeAgentModel()
    decision = await model.plan(
        "Which bank had the higher Standardized CET1 at year-end 2024, "
        "JPMorgan or Bank of America?",
        [],
        [],
    )
    assert decision.kind == "plan"
    assert all(call.name == "search_filings" for call in decision.calls)


async def test_fake_planner_uses_graph_for_subsidiary_rssd() -> None:
    model = FakeAgentModel()
    decision = await model.plan(
        "What is the RSSD of the national bank subsidiary of JPM?",
        [],
        [],
    )
    assert decision.kind == "plan"
    graph = next(call for call in decision.calls if call.name == "graph_query")
    assert graph.args["kind"] == "subsidiaries"
    assert graph.args["ticker"] == "JPM"


async def test_run_agent_calls_graph_query_on_the_first_plan() -> None:
    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    tools = default_registry(
        search=_empty_search, xbrl=_missing_xbrl, fdic=_missing_fdic, graph=store
    )
    result = await run_agent(
        "What is the RSSD of the national bank subsidiary of JPM?",
        FakeAgentModel(),
        tools,
        Budgets(max_steps=8, max_tokens=8000),
    )
    assert any(step.tool == "graph_query" for step in result.plan)
    assert any("852218" in step.detail for step in result.traces)


def test_cli_graph_load_memory_twice() -> None:
    first = runner.invoke(app, ["graph", "load", "--backend", "memory"])
    second = runner.invoke(app, ["graph", "load", "--backend", "memory"])
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "banks=8" in first.stdout
    assert first.stdout == second.stdout


def test_cli_graph_query_jpm_subsidiaries() -> None:
    result = runner.invoke(app, ["graph", "query", "--kind", "subsidiaries", "--ticker", "JPM"])
    assert result.exit_code == 0
    assert "852218" in result.stdout


def test_cli_graph_load_neo4j_without_uri_is_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("NEO4J_URI", "")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["graph", "load", "--backend", "neo4j"])
        assert result.exit_code == 2
        assert "unverified" in result.stdout + result.stderr
    finally:
        get_settings.cache_clear()


def test_gate_denies_unknown_graph_kind() -> None:
    from capitalcite.agent.tools import ToolRegistry
    from capitalcite.gate import DenyReason, Gate, load_policy

    gate = Gate(load_policy(None), ToolRegistry({}))
    denied = gate.decide("graph_query", {"kind": "cypher", "ticker": "JPM"})
    allowed = gate.decide("graph_query", {"kind": "subsidiaries", "ticker": "JPM"})
    unknown = gate.decide("shell", {"cmd": "ls"})
    assert denied.kind == "deny"
    assert denied.reason == DenyReason.invalid_args
    assert allowed.kind == "allow"
    assert unknown.kind == "deny"
    assert unknown.reason == DenyReason.unknown_tool


async def test_partial_reload_keeps_existing_banks() -> None:
    from capitalcite.kg import Bank, GraphSnapshot

    store = MemoryGraphStore()
    await store.load(load_snapshot(_SNAPSHOT))
    await store.load(GraphSnapshot(banks=(Bank(rssd="9999999", name="ONLY"),)))
    result = await store.query(GraphQuery(kind=GraphQueryKind.SUBSIDIARIES, ticker="JPM"))
    assert result.ok is True
    assert "852218" in result.detail


async def test_neo4j_statements_merge_and_honor_depth() -> None:
    from capitalcite.kg import Neo4jGraphStore

    log: list[str] = []

    class _Result:
        def __aiter__(self) -> _Result:
            return self

        async def __anext__(self) -> object:
            raise StopAsyncIteration

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def run(self, cypher: str, **_params: object) -> _Result:
            log.append(cypher)
            return _Result()

    class _Driver:
        def session(self) -> _Session:
            return _Session()

    store = Neo4jGraphStore("bolt://127.0.0.1:1", "neo4j", "unused", driver=_Driver())
    receipt = await store.load(load_snapshot(_SNAPSHOT))
    assert receipt.backend == "neo4j"
    assert receipt.edges == 4
    loaded = list(log)
    assert loaded
    assert all("MERGE" in line for line in loaded)
    assert all("CREATE " not in line for line in loaded)
    result = await store.query(GraphQuery(kind=GraphQueryKind.SUBSIDIARIES, ticker="JPM", depth=1))
    assert result.ok is False
    assert any("PARENT_OF*1..1" in line for line in log)


async def test_graph_eval_small_is_perfect_on_snapshot() -> None:
    report = await run_graph_suite(out_dir=None)
    assert report.n >= 5
    assert report.graph_exact == 1.0
    assert report.fixture_substring_hit_rate == 0.0
    assert report.delta == 1.0


def test_cli_eval_graph_small(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["eval", "--suite", "graph", "--small", "--out", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert "graph_exact=1.000" in result.stdout
    assert "fixture_substring_hit_rate=0.000" in result.stdout


async def _empty_search(
    query: str, *, k: int | None = None, filters: SearchFilters | None = None
) -> list[Hit]:
    _ = query, k, filters
    return []


async def _missing_xbrl(ticker: str, concept: str, period: str | None) -> str:
    raise ValueError(f"no xbrl fact for {ticker} {concept}")


async def _missing_fdic(query: str) -> str:
    raise ValueError(f"no FDIC institution matched {query!r}")
