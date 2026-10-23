from __future__ import annotations

from pathlib import Path

from mcp import Client
from qdrant_client import AsyncQdrantClient
from typer.testing import CliRunner

from capitalcite.agent.tools import default_registry
from capitalcite.audit import AuditLog, AuditRecord, verify
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.cli import app
from capitalcite.config import Settings
from capitalcite.gate import Policy
from capitalcite.mcp import MCP_ONLY_TOOLS, MCP_TOOL_NAMES, McpSession, build_server
from capitalcite.retrieval import FakeEmbedder, RetrievalSetup, build_index, setup_retrieval
from capitalcite.retrieval.types import Embedder, Hit, SearchFilters, TextEmbedding
from capitalcite.trace import MemoryExporter, ObservationKind, configure_for_tests, reset_for_tests

runner = CliRunner()
_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"


def test_cli_help_lists_mcp() -> None:
    result = runner.invoke(app, ["mcp", "--help"], color=False)
    assert result.exit_code == 0
    assert "stdio" in result.stdout.lower() or "MCP" in result.stdout or "mcp" in result.stdout


def test_mcp_names_are_search_ask_and_metric_query() -> None:
    assert {"search_filings", "ask", "metric_query"} == MCP_TOOL_NAMES
    assert {"ask"} == MCP_ONLY_TOOLS


async def test_mcp_lists_search_ask_and_metric_query(tmp_path: Path) -> None:
    session, _setup = await _indexed_session(tmp_path)
    async with Client(build_server(session)) as client:
        listed = await client.list_tools()
    assert {tool.name for tool in listed.tools} == {"ask", "metric_query", "search_filings"}
    by_name = {tool.name: tool for tool in listed.tools}
    for name in ("ask", "metric_query", "search_filings"):
        schema = by_name[name].input_schema
        assert schema.get("additionalProperties") is False
    assert isinstance(session.policy, Policy)
    assert set(session.policy.tools) == MCP_TOOL_NAMES


async def test_mcp_search_returns_citations_and_audit(tmp_path: Path) -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    try:
        session, _setup = await _indexed_session(tmp_path)
        async with Client(build_server(session)) as client:
            result = await client.call_tool(
                "search_filings",
                {"query": "tier one capital", "ticker": "JPM"},
            )
        body = result.structured_content
        assert result.is_error is False
        assert isinstance(body, dict)
        assert body["ok"] is True
        hits = body["hits"]
        assert isinstance(hits, list)
        assert hits
        assert hits[0]["chunk"]["doc_id"] == "doc-jpm"
        assert hits[0]["chunk"]["section"] == "Item 15"
        report = verify(session.log.path)
        assert report.ok is True
        assert report.records == 1
        record = _records(session.log.path)[0]
        assert record.tool == "search_filings"
        assert record.decision == "allowed"
        kinds = {span.name: span.kind for span in exporter.finished()}
        assert kinds.get("gate") == ObservationKind.GATE
        assert "search_filings" in kinds
    finally:
        reset_for_tests()


class _QuerySpy:
    def __init__(self, inner: Embedder, calls: list[str]) -> None:
        self._inner = inner
        self._calls = calls
        self.id = inner.id
        self.dense_size = inner.dense_size
        self.sparse_id = inner.sparse_id

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> TextEmbedding:
        self._calls.append(text)
        return await self._inner.embed_query(text)

    def sparse_query_terms(self, text: str) -> tuple[str, ...]:
        return self._inner.sparse_query_terms(text)


async def test_mcp_extra_keys_are_denied_and_do_not_search(tmp_path: Path) -> None:
    calls: list[str] = []
    session, setup = await _indexed_session(tmp_path)
    setup.embedder = _QuerySpy(setup.embedder, calls)
    async with Client(build_server(session)) as client:
        result = await client.call_tool(
            "search_filings",
            {"query": "tier one capital", "cmd": "ls"},
        )
    body = result.structured_content
    assert isinstance(body, dict)
    assert body["ok"] is False
    assert body["reason"] == "invalid_args"
    assert calls == []
    record = _records(session.log.path)[0]
    assert record.decision == "denied"
    assert record.reason == "invalid_args"


async def test_mcp_calculator_is_unknown_tool(tmp_path: Path) -> None:
    session, _setup = await _indexed_session(tmp_path)
    async with Client(build_server(session)) as client:
        result = await client.call_tool("calculator", {"expression": "1+1"})
    body = result.structured_content
    assert isinstance(body, dict)
    assert body["ok"] is False
    assert body["reason"] == "unknown_tool"
    assert _records(session.log.path)[0].decision == "denied"


async def test_mcp_graph_query_is_unknown_tool(tmp_path: Path) -> None:
    session, _setup = await _indexed_session(tmp_path)
    async with Client(build_server(session)) as client:
        result = await client.call_tool("graph_query", {"kind": "subsidiaries", "ticker": "JPM"})
    body = result.structured_content
    assert isinstance(body, dict)
    assert body["ok"] is False
    assert body["reason"] == "unknown_tool"
    assert _records(session.log.path)[0].decision == "denied"


async def test_mcp_ask_answers_with_grounded_citation(tmp_path: Path) -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    try:
        session, _setup = await _indexed_session(tmp_path)
        async with Client(build_server(session)) as client:
            result = await client.call_tool(
                "ask",
                {"question": "tier one capital", "ticker": "JPM", "model": "fake"},
            )
        body = result.structured_content
        assert isinstance(body, dict)
        assert body["ok"] is True
        assert body["kind"] == "answered"
        assert body["citations"]
        cite = body["citations"][0]
        assert cite["doc_id"] == "doc-jpm"
        assert cite["section"] == "Item 15"
        assert "tier one capital" in cite["quote"]
        record = _records(session.log.path)[0]
        assert record.tool == "ask"
        assert record.decision == "allowed"
        names = {span.name for span in exporter.finished()}
        assert "ask" in names
        assert "gate" in names
        assert "retrieve" in names
    finally:
        reset_for_tests()


async def test_mcp_ask_refuses_unanswerable(tmp_path: Path) -> None:
    session, _setup = await _indexed_session(tmp_path)
    async with Client(build_server(session)) as client:
        result = await client.call_tool(
            "ask",
            {
                "question": "What CET1 ratio did Wells Fargo disclose for year-end 2026?",
                "ticker": "WFC",
                "model": "fake",
            },
        )
    body = result.structured_content
    assert isinstance(body, dict)
    assert body["ok"] is True
    assert body["kind"] == "refused"
    assert body["citations"] == []
    assert body["refusal_reason"]
    assert _records(session.log.path)[0].decision == "allowed"


async def test_mcp_ask_extra_keys_are_denied(tmp_path: Path) -> None:
    session, _setup = await _indexed_session(tmp_path)
    async with Client(build_server(session)) as client:
        result = await client.call_tool(
            "ask",
            {"question": "tier one capital", "cmd": "ls", "model": "fake"},
        )
    body = result.structured_content
    assert isinstance(body, dict)
    assert body["ok"] is False
    assert body["reason"] == "invalid_args"
    record = _records(session.log.path)[0]
    assert record.decision == "denied"
    assert record.reason == "invalid_args"
    assert record.tool == "ask"


async def test_agent_registry_does_not_run_ask() -> None:
    ran: list[str] = []

    async def search(
        query: str,
        *,
        k: int | None = None,
        filters: SearchFilters | None = None,
    ) -> list[Hit]:
        ran.append("search")
        _ = query, k, filters
        return []

    async def missing_xbrl(ticker: str, concept: str, period: str | None) -> str:
        ran.append("http")
        _ = ticker, concept, period
        return "no"

    async def missing_fdic(query: str) -> str:
        ran.append("http")
        _ = query
        return "no"

    registry = default_registry(search=search, xbrl=missing_xbrl, fdic=missing_fdic)
    result = await registry.execute("ask", {"question": "cet1"})
    assert result.ok is False
    assert result.detail == "unknown tool"
    assert ran == []


def test_mcp_domain_does_not_import_fastapi() -> None:
    root = Path("src/capitalcite/mcp")
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "fastapi" not in text, f"{path} mentions fastapi"


async def _indexed_session(tmp_path: Path) -> tuple[McpSession, RetrievalSetup]:
    _write_chunks(tmp_path, [_chunk()])
    client = AsyncQdrantClient(":memory:")
    settings = _settings(audit_log_path=tmp_path / "audit.jsonl")
    setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
    await build_index(setup, tmp_path)
    session = McpSession(
        settings=settings,
        setup=setup,
        log=AuditLog(settings.audit_log_path),
    )
    return session, setup


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
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
        "audit_log_path": Path("data/audit.jsonl"),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _chunk() -> Chunk:
    return Chunk(
        doc_id="doc-jpm",
        section="Item 15",
        start=10,
        end=40,
        offset=10,
        source_url=_JPM_URL,
        text="tier one capital buffer",
        token_count=4,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2023-12-31",
        accession="doc-jpm",
    )


def _write_chunks(root: Path, chunks: list[Chunk]) -> None:
    folder = root / "parsed" / "edgar" / "JPM" / "10-K" / "2023-12-31" / "acc"
    folder.mkdir(parents=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _records(path: Path) -> list[AuditRecord]:
    return [
        AuditRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
