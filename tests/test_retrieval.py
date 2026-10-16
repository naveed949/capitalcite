from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from typer.testing import CliRunner

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.cli import app
from capitalcite.config import Settings
from capitalcite.golden.models import Evidence
from capitalcite.golden.store import load_items
from capitalcite.retrieval import (
    EmbedderName,
    FakeEmbedder,
    Hit,
    IndexConfig,
    IndexNotBuilt,
    RetrievalMode,
    build_index,
    collection_name,
    config_hash,
    parse_filters,
    point_id,
    reciprocal_rank_fusion,
    retrieve,
    setup_retrieval,
    span_overlap,
    term_counts,
    year_of,
)
from capitalcite.retrieval.embedders import FastEmbedEmbedder, spec_for
from capitalcite.retrieval.sanity import run_quote_index_sanity
from capitalcite.retrieval.types import (
    CorruptChunks,
    EmbedderNotConfigured,
    ExactPeriod,
    YearPeriod,
)

runner = CliRunner()

_JPM_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"
_BAC_URL = "https://www.sec.gov/Archives/edgar/data/70858/bac.htm"


def test_config_hash_is_canonical_json() -> None:
    config = IndexConfig(
        embedder_id="fake:deterministic",
        dense_size=8,
        chunker=ChunkerName.SECTION,
        chunk_size=32,
        chunk_overlap=4,
        collection_prefix="filings",
    )
    raw = json.dumps(
        config.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    body = json.loads(raw)
    assert config_hash(config) == digest
    assert collection_name(config) == f"filings-{digest}"
    assert body["chunk_overlap"] == 4
    assert body["distance"] == "cosine"
    assert body["dense_vector"] == "dense"
    assert body["sparse_vector"] == "bm25"
    assert body["sparse_modifier"] == "idf"
    changed = config.model_copy(update={"chunk_overlap": 0})
    assert config_hash(changed) != digest


def test_point_id_is_stable() -> None:
    assert (
        point_id("0000019617-24-000225", "section", 488083, 488140)
        == "7ded70f7-8d13-5d8d-86cd-3028fd885fb6"
    )


def test_reciprocal_rank_fusion_literal() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    assert fused == [
        ("b", 1 / 61 + 1 / 62),
        ("a", 1 / 61),
        ("c", 1 / 62),
    ]


def test_term_counts_are_sorted_counts() -> None:
    sparse = term_counts("Alpha alpha BETA")
    assert sparse.indices == (2933053284, 4133809390)
    assert sparse.values == (1.0, 2.0)


def test_year_of_and_parse_filters() -> None:
    assert year_of("2023-12-31") == 2023
    assert year_of("2024") == 2024
    assert year_of("FY2024") is None
    assert year_of("") is None
    filters = parse_filters(ticker=" jpm ", form="10-k", period="2023")
    assert filters.ticker == "JPM"
    assert filters.form == "10-K"
    assert isinstance(filters.period, YearPeriod)
    assert filters.period.year == 2023
    exact = parse_filters(period="2023-12-31")
    assert isinstance(exact.period, ExactPeriod)
    assert exact.period.period == "2023-12-31"
    assert parse_filters(ticker="  ").ticker is None
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_filters(period="FY2023")
    with pytest.raises(ValueError, match="out of range"):
        parse_filters(period="1800")


def test_hit_year_comes_from_period() -> None:
    hit = Hit(score=0.5, chunk=_chunk(period="2023-12-31"))
    assert hit.year == 2023
    assert "year" not in hit.chunk.model_dump()
    assert hit.model_dump()["year"] == 2023


def test_span_overlap_cc_h_001() -> None:
    item = next(row for row in load_items(Path("evals/golden/v1.jsonl")) if row.id == "cc-h-001")
    evidence = item.evidence[0]
    assert evidence.doc_id == "0000019617-24-000225"
    assert evidence.start == 488083
    assert evidence.end == 488140
    covering = _chunk(
        doc_id="0000019617-24-000225",
        start=488000,
        end=488200,
        offset=488000,
        text="cet1",
    )
    assert span_overlap(covering, evidence) is True
    touching = covering.model_copy(update={"start": 488140, "end": 488200, "offset": 488140})
    assert span_overlap(touching, evidence) is False
    other_doc = covering.model_copy(update={"doc_id": "other"})
    assert span_overlap(other_doc, evidence) is False
    bare = Evidence(doc_id=evidence.doc_id, section="Item 15", quote="15.0%")
    assert span_overlap(covering, bare) is False


def test_embedder_names_match_settings() -> None:
    allowed = set(get_args(Settings.model_fields["embedder"].annotation))
    assert allowed == {name.value for name in EmbedderName}
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "sec_user_agent": "capitalcite-tests tests@example.com",
                "embedder": "nope",
            }
        )


def test_openai_embedder_requires_key() -> None:
    settings = _settings(embedder="openai-small", openai_api_key=None)
    with pytest.raises(EmbedderNotConfigured, match="OPENAI_API_KEY"):
        from capitalcite.retrieval import create_retrieval_setup

        create_retrieval_setup(settings)


async def test_fastembed_does_not_run_under_pytest() -> None:
    embedder = FastEmbedEmbedder(spec_for(EmbedderName.BGE_SMALL))
    with pytest.raises(RuntimeError, match="FastEmbed must not run"):
        await embedder.embed_query("cet1")


async def test_retrieve_dense_bm25_hybrid_and_filters(tmp_path: Path) -> None:
    jpm = _chunk(
        doc_id="doc-jpm",
        text="tier one capital buffer",
        ticker="JPM",
        period="2023-12-31",
        source_url=_JPM_URL,
        start=10,
        end=40,
        offset=10,
    )
    bac = _chunk(
        doc_id="doc-bac",
        text="wholesale funding markets",
        ticker="BAC",
        period="2024-12-31",
        source_url=_BAC_URL,
        start=10,
        end=40,
        offset=10,
    )
    _write_chunks(tmp_path, [jpm, bac])
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        receipt = await build_index(setup, tmp_path)
        assert receipt.point_count == 2
        dense = await retrieve(setup, "tier one capital", mode=RetrievalMode.DENSE, k=5)
        bm25 = await retrieve(setup, "tier one capital", mode=RetrievalMode.BM25, k=5)
        hybrid = await retrieve(setup, "tier one capital", mode=RetrievalMode.HYBRID, k=5)
        assert dense[0].chunk.doc_id == "doc-jpm"
        assert [hit.chunk.doc_id for hit in bm25] == ["doc-jpm"]
        assert [hit.chunk.doc_id for hit in hybrid] == ["doc-jpm", "doc-bac"]
        # Qdrant RRF ranks from zero, so rank 1 on both legs is 1/60 + 1/60.
        assert hybrid[0].score == 1 / 30
        assert hybrid[1].score == 1 / 61
        assert hybrid[0].chunk.text == "tier one capital buffer"
        assert hybrid[0].chunk.start == 10
        assert hybrid[0].chunk.end == 40
        assert hybrid[0].chunk.source_url == _JPM_URL
        assert hybrid[0].year == 2023
        filtered = await retrieve(
            setup,
            "tier one capital",
            mode=RetrievalMode.HYBRID,
            k=5,
            filters=parse_filters(ticker="jpm"),
        )
        assert [hit.chunk.doc_id for hit in filtered] == ["doc-jpm"]
        assert filtered[0].chunk.ticker == "JPM"
        by_year = await retrieve(
            setup,
            "tier one capital",
            mode=RetrievalMode.HYBRID,
            k=5,
            filters=parse_filters(period="2024"),
        )
        assert [hit.chunk.doc_id for hit in by_year] == ["doc-bac"]
        assert by_year[0].year == 2024
    finally:
        await client.close()


async def test_rebuild_is_idempotent_and_drops_orphans(tmp_path: Path) -> None:
    first = _chunk(doc_id="doc-keep", text="tier one capital buffer")
    second = _chunk(doc_id="doc-drop", text="wholesale funding markets", ticker="BAC")
    _write_chunks(tmp_path, [first, second])
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        built = await build_index(setup, tmp_path)
        again = await build_index(setup, tmp_path)
        sidecar = tmp_path / "index" / f"{built.collection_name}.json"
        first_bytes = sidecar.read_text(encoding="utf-8")
        assert again.point_count == 2
        assert again.config_hash == built.config_hash
        assert sidecar.read_text(encoding="utf-8") == first_bytes
        _write_chunks(tmp_path, [first])
        trimmed = await build_index(setup, tmp_path)
        assert trimmed.point_count == 1
        hits = await retrieve(setup, "wholesale funding", mode=RetrievalMode.BM25, k=5)
        assert [hit.chunk.doc_id for hit in hits] == []
        kept = await retrieve(setup, "tier one capital", mode=RetrievalMode.BM25, k=5)
        assert [hit.chunk.doc_id for hit in kept] == ["doc-keep"]
    finally:
        await client.close()


async def test_corrupt_line_does_not_write_sidecar(tmp_path: Path) -> None:
    folder = tmp_path / "parsed" / "edgar" / "JPM" / "10-K" / "2023-12-31" / "acc"
    folder.mkdir(parents=True)
    (folder / "chunks.jsonl").write_text("{not json}\n", encoding="utf-8")
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        with pytest.raises(CorruptChunks):
            await build_index(setup, tmp_path)
        assert list((tmp_path / "index").glob("*.json")) == []
    finally:
        await client.close()


async def test_chunker_mismatch_fails(tmp_path: Path) -> None:
    _write_chunks(tmp_path, [_chunk(chunker=ChunkerName.FIXED)])
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        with pytest.raises(CorruptChunks, match="chunker"):
            await build_index(setup, tmp_path)
        assert not (tmp_path / "index").exists()
    finally:
        await client.close()


async def test_missing_collection_raises_index_not_built() -> None:
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        with pytest.raises(IndexNotBuilt):
            await retrieve(setup, "tier one capital", mode=RetrievalMode.HYBRID, k=5)
    finally:
        await client.close()


async def test_quote_chunk_retrieves_cc_h_001_span(tmp_path: Path) -> None:
    item = next(row for row in load_items(Path("evals/golden/v1.jsonl")) if row.id == "cc-h-001")
    evidence = item.evidence[0]
    quote = _chunk(
        doc_id="0000019617-24-000225",
        section="Item 15",
        start=488083,
        end=488140,
        offset=488083,
        text=evidence.quote,
        source_url=_JPM_URL,
        accession="0000019617-24-000225",
    )
    distractor = _chunk(
        doc_id="doc-other",
        text="wholesale funding markets",
        ticker="BAC",
        period="2024-12-31",
    )
    _write_chunks(tmp_path, [quote, distractor])
    client = AsyncQdrantClient(":memory:")
    try:
        setup = setup_retrieval(_settings(), client=client, embedder=FakeEmbedder())
        await build_index(setup, tmp_path)
        hits = await retrieve(setup, item.question, mode=RetrievalMode.HYBRID, k=5)
        assert hits[0].chunk.doc_id == "0000019617-24-000225"
        assert hits[0].chunk.start == 488083
        assert hits[0].chunk.end == 488140
        assert span_overlap(hits[0].chunk, evidence) is True
    finally:
        await client.close()


async def test_quote_index_sanity_counts_handwritten_spans() -> None:
    from capitalcite.golden.store import load_golden_set

    golden = load_golden_set(Path("evals/golden"))
    expected = sum(
        1
        for item in golden.items
        if item.in_recall
        and any(row.start is not None and row.end is not None for row in item.evidence)
    )
    report = await run_quote_index_sanity(Path("evals/golden"))
    assert report.mode == "hybrid"
    assert report.items == expected
    assert report.hits == len(report.hit_ids)
    assert report.recall_at_5 == report.hits / report.items


def test_index_help() -> None:
    result = runner.invoke(app, ["index", "--help"])
    assert result.exit_code == 0
    assert "--embedder" in result.stdout
    assert "--input-dir" in result.stdout
    assert "--chunker" in result.stdout
    assert "--overlap" in result.stdout


def test_index_cli_builds_and_records_overlap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncQdrantClient(":memory:")
    _write_chunks(tmp_path, [_chunk(text="tier one capital buffer")])

    def factory(settings: Settings) -> object:
        return setup_retrieval(settings, client=client, embedder=FakeEmbedder())

    monkeypatch.setattr("capitalcite.cli.create_retrieval_setup", factory)
    result = runner.invoke(
        app,
        ["index", "--input-dir", str(tmp_path), "--size", "32", "--overlap", "4"],
    )
    assert result.exit_code == 0
    assert "points=1" in result.stdout
    sidecars = list((tmp_path / "index").glob("*.json"))
    assert len(sidecars) == 1
    body = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert body["point_count"] == 1
    assert body["canonical"]["chunk_size"] == 32
    assert body["canonical"]["chunk_overlap"] == 4
    assert body["config_hash"] in result.stdout


def test_index_openai_without_key_exits_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(embedder="openai-small", openai_api_key=None)
    monkeypatch.setattr("capitalcite.cli.get_settings", lambda: settings)
    result = runner.invoke(app, ["index", "--input-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def _settings(**overrides: object) -> Settings:
    data: dict[str, object] = {
        "sec_user_agent": "capitalcite-tests tests@example.com",
        "chunker": ChunkerName.SECTION,
        "chunk_size": 32,
        "chunk_overlap": 4,
        "qdrant_collection": "filings",
        "embedder": "bge-small",
        "search_k": 8,
        "rrf_k": 60,
        "openai_api_key": None,
    }
    data.update(overrides)
    return Settings(**data)  # type: ignore[arg-type]


def _chunk(**overrides: object) -> Chunk:
    data: dict[str, object] = {
        "doc_id": "doc-jpm",
        "section": "Item 15",
        "start": 10,
        "end": 40,
        "offset": 10,
        "source_url": _JPM_URL,
        "text": "tier one capital buffer",
        "token_count": 4,
        "chunker": ChunkerName.SECTION,
        "ticker": "JPM",
        "form": "10-K",
        "period": "2023-12-31",
        "accession": "doc-jpm",
    }
    data.update(overrides)
    return Chunk(**data)  # type: ignore[arg-type]


def _write_chunks(root: Path, chunks: list[Chunk]) -> None:
    folder = root / "parsed" / "edgar" / "JPM" / "10-K" / "2023-12-31" / "acc"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
