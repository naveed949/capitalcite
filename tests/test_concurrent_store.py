from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
_BODY = {"question": "What is the tier 1 capital buffer?", "model": "fake"}


@pytest.mark.asyncio
async def test_concurrent_ask_and_health_share_one_embedded_store(
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
    embedder = _IndexEmbedder()
    setup = setup_retrieval(settings, client=client, embedder=embedder)
    try:
        await build_index(setup, tmp_path / "chunks")
    finally:
        await setup.aclose()

    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("QDRANT_PATH", str(store))
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ASK_MODEL", "fake")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    monkeypatch.setattr(
        "capitalcite.retrieval.service.build_embedder",
        lambda *_args, **_kwargs: embedder,
    )
    get_settings.cache_clear()
    application = create_app()
    with TestClient(application) as http:

        @dataclass(frozen=True)
        class Row:
            name: str
            status: int
            detail: str

        def ask() -> Row:
            response = http.post("/ask", json=_BODY)
            return Row("ask", response.status_code, str(response.json().get("kind")))

        def stream() -> Row:
            response = http.post("/ask/stream", json=_BODY)
            return Row("stream", response.status_code, response.headers.get("content-type") or "")

        def health() -> Row:
            response = http.get("/health")
            return Row("health", response.status_code, str(response.json()["point_count"]))

        jobs = []
        with ThreadPoolExecutor(max_workers=12) as pool:
            for _ in range(4):
                jobs.append(pool.submit(ask))
                jobs.append(pool.submit(stream))
                jobs.append(pool.submit(health))
            results = [future.result() for future in as_completed(jobs)]

    asks = [row for row in results if row.name == "ask"]
    streams = [row for row in results if row.name == "stream"]
    healths = [row for row in results if row.name == "health"]
    assert len(asks) == 4
    assert len(streams) == 4
    assert len(healths) == 4
    for row in asks:
        assert row.status == 200
        assert row.detail in {"answered", "refused"}
    for row in streams:
        assert row.status == 200
        assert "text/event-stream" in row.detail
    for row in healths:
        assert row.status == 200
        assert int(row.detail) == len(chunks)


class _IndexEmbedder:
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
