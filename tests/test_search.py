from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.api.app import create_app
from capitalcite.api.routes.search import get_setup
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import (
    FakeEmbedder,
    build_index,
    collection_name,
    config_hash,
    setup_retrieval,
)

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"


def test_post_search_returns_citations(tmp_path: Path) -> None:
    chunk = _chunk()
    _write_chunks(tmp_path, [chunk])
    client = AsyncQdrantClient(":memory:")
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    with TestClient(application) as http:
        health = http.get("/health")
        response = http.post(
            "/search",
            json={
                "query": "tier one capital",
                "mode": "hybrid",
                "k": 5,
                "ticker": "jpm",
                "form": "10-k",
                "period": "2023",
            },
        )
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert response.status_code == 200
    body = response.json()
    assert body["point_count"] == 1
    assert body["config_hash"] == config_hash(setup.config)
    assert body["collection_name"] == collection_name(setup.config)
    assert len(body["config_hash"]) == 64
    hit = body["hits"][0]
    # One chunk is rank 1 on both legs. Qdrant scores that as 1/60 + 1/60.
    assert hit["score"] == 1 / 30
    assert hit["year"] == 2023
    assert hit["chunk"]["doc_id"] == "doc-jpm"
    assert hit["chunk"]["section"] == "Item 15"
    assert hit["chunk"]["start"] == 10
    assert hit["chunk"]["end"] == 40
    assert hit["chunk"]["source_url"] == _JPM_URL
    assert hit["chunk"]["text"] == "tier one capital buffer"


def test_post_search_empty_index_is_200(tmp_path: Path) -> None:
    client = AsyncQdrantClient(":memory:")
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    asyncio.run(build_index(setup, tmp_path))
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    with TestClient(application) as http:
        response = http.post("/search", json={"query": "cet1"})
    assert response.status_code == 200
    body = response.json()
    assert body["point_count"] == 0
    assert body["hits"] == []


def test_post_search_rejects_bad_period() -> None:
    client = AsyncQdrantClient(":memory:")
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    with TestClient(application) as http:
        response = http.post("/search", json={"query": "cet1", "period": "FY2024"})
    assert response.status_code == 422
    assert response.json()["detail"] == "period must be YYYY-MM-DD or a 4-digit year"


def test_shared_client_survives_a_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "qdrant"
    store.mkdir()
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", str(store))
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ASK_MODEL", "fake")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    get_settings.cache_clear()
    application = create_app()
    with TestClient(application) as http:
        first = http.get("/health")
        setup = application.state.retrieval
        second = http.get("/health")
        assert first.status_code == 200
        assert second.status_code == 200
        assert setup is application.state.retrieval
        assert setup is not None
    assert application.state.retrieval is None


def test_post_search_503_when_qdrant_is_down() -> None:
    client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1, check_compatibility=False)
    setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
    application = create_app()
    application.dependency_overrides[get_setup] = lambda: setup
    with TestClient(application) as http:
        response = http.post("/search", json={"query": "cet1"})
    assert response.status_code == 503


def test_api_and_cli_do_not_import_store_clients() -> None:
    root = Path("src/capitalcite")
    files = (
        root / "cli.py",
        root / "api" / "app.py",
        root / "api" / "routes" / "search.py",
        root / "api" / "routes" / "ask.py",
        root / "api" / "routes" / "health.py",
    )
    banned = ("qdrant_client", "fastembed", "from neo4j", "import neo4j")
    for path in files:
        text = path.read_text(encoding="utf-8")
        for needle in banned:
            assert needle not in text, f"{path} mentions {needle}"


def test_health_stays_up_when_the_store_cannot_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("capitalcite.retrieval.service.AsyncQdrantClient", boom)
    application = create_app()
    with TestClient(application) as http:
        response = http.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["point_count"] == 0


def _settings() -> Settings:
    return Settings(
        sec_user_agent="capitalcite-tests tests@example.com",
        chunker=ChunkerName.SECTION,
        chunk_size=32,
        chunk_overlap=4,
        qdrant_collection="filings",
        embedder="bge-small",
        search_k=8,
        rrf_k=60,
        openai_api_key=None,
    )


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
