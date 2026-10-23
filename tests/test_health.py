from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient

from capitalcite.api.app import create_app
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings, get_settings
from capitalcite.retrieval import build_index, setup_retrieval
from capitalcite.retrieval.embedders import spec_for
from capitalcite.retrieval.sparse import FASTEMBED_SPARSE_ID, bm25_document, bm25_query, bm25_terms
from capitalcite.retrieval.types import EmbedderName, TextEmbedding

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"


def test_health_without_index_is_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_store(monkeypatch, tmp_path / "empty")
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "capitalcite"
    assert isinstance(body["version"], str) and body["version"].strip()
    assert isinstance(body["git_sha"], str) and body["git_sha"].strip()
    assert isinstance(body["point_count"], int) and body["point_count"] == 0


def test_health_git_sha_uses_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_store(monkeypatch, tmp_path / "empty")
    monkeypatch.setenv("GIT_SHA", "abc123def")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["git_sha"] == "abc123def"


def test_health_blank_git_sha_is_not_blank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _use_store(monkeypatch, tmp_path / "empty")
    monkeypatch.setenv("GIT_SHA", "   ")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    sha = response.json()["git_sha"]
    assert isinstance(sha, str) and sha.strip()
    assert sha != "   "


def test_health_point_count_matches_indexed_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "qdrant"
    store.mkdir()
    chunks = [
        _chunk(start=0, text="tier one capital buffer"),
        _chunk(start=40, text="allowance for credit losses"),
    ]
    _write_chunks(tmp_path / "chunks", chunks)
    settings = Settings(
        sec_user_agent="capitalcite-tests tests@example.com",
        qdrant_path=store,
    )
    client = AsyncQdrantClient(path=str(store), timeout=5, check_compatibility=False)
    setup = setup_retrieval(settings, client=client, embedder=_IndexEmbedder())

    async def build() -> None:
        try:
            await build_index(setup, tmp_path / "chunks")
        finally:
            await setup.aclose()

    asyncio.run(build())
    _use_store(monkeypatch, store)
    with TestClient(create_app()) as http:
        response = http.get("/health")

    assert response.status_code == 200
    assert response.json()["point_count"] == len(chunks)


def _use_store(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", str(store))
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ASK_MODEL", "fake")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    get_settings.cache_clear()


class _IndexEmbedder:
    """Same index identity as EMBEDDER=bge-small. Pytest must not load FastEmbed."""

    def __init__(self) -> None:
        spec = spec_for(EmbedderName.BGE_SMALL)
        self.id = spec.embedder_id
        self.dense_size = spec.dense_size
        self.sparse_id = FASTEMBED_SPARSE_ID

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        return [
            TextEmbedding(dense=_dense(text, self.dense_size), sparse=bm25_document(text))
            for text in texts
        ]

    async def embed_query(self, text: str) -> TextEmbedding:
        return TextEmbedding(dense=_dense(text, self.dense_size), sparse=bm25_query(text))

    def sparse_query_terms(self, text: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(bm25_terms(text)))


def _dense(text: str, width: int) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return tuple(digest[index % len(digest)] / 255.0 for index in range(width))


def _chunk(*, start: int, text: str) -> Chunk:
    end = start + len(text)
    return Chunk(
        doc_id="doc-jpm",
        section="Item 1",
        start=start,
        end=end,
        offset=start,
        source_url=_JPM_URL,
        text=text,
        token_count=4,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2024-12-31",
        accession="doc-jpm",
    )


def _write_chunks(root: Path, chunks: list[Chunk]) -> None:
    folder = root / "parsed" / "edgar" / "JPM" / "10-K" / "2024-12-31" / "acc"
    folder.mkdir(parents=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
