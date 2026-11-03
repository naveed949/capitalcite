from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from capitalcite.ask.models import FakeStructuredModel
from capitalcite.ask.service import ask
from capitalcite.ask.types import Answered, Refused
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens
from capitalcite.config import Settings
from capitalcite.golden.models import Evidence, GoldenItem
from capitalcite.golden.store import load_items
from capitalcite.retrieval.embedders import FakeEmbedder
from capitalcite.retrieval.service import RetrievalSetup, build_index, retrieve, setup_retrieval
from capitalcite.retrieval.types import RetrievalMode
from capitalcite.trace import MemoryExporter, configure_for_tests, reset_for_tests

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "evals" / "golden" / "v1.jsonl"
EXPECTED = 112


def main(argv: list[str] | None = None) -> int:
    _ = argv
    model = os.environ.get("ASK_MODEL", "fake")
    if model != "fake":
        print("ASK_MODEL must be fake", file=sys.stderr)
        return 2
    try:
        items = load_items(ITEMS)
    except (OSError, ValueError) as exc:
        print(f"ask-trace-diff could not load {ITEMS}: {exc}", file=sys.stderr)
        return 2
    print(f"ask-trace-diff items={len(items)}")
    if len(items) != EXPECTED:
        print(f"expected {EXPECTED} golden questions", file=sys.stderr)
        return 1
    try:
        mismatches = asyncio.run(_compare(items))
    except Exception as exc:
        print(f"ask-trace-diff failed: {exc}", file=sys.stderr)
        return 1
    print(f"ask-trace-diff mismatches={mismatches}")
    return 0 if mismatches == 0 else 1


async def _compare(items: list[GoldenItem]) -> int:
    chunks = _chunks(items)
    settings = Settings(
        sec_user_agent="capitalcite-trace-diff trace@example.com",
        chunker=ChunkerName.SECTION,
        chunk_size=512,
        chunk_overlap=64,
        qdrant_collection="trace-diff",
        embedder="bge-small",
        search_k=8,
        rrf_k=60,
        ask_model="fake",
        langfuse_tracing_enabled=False,
    )
    client = AsyncQdrantClient(":memory:")
    mismatches = 0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_quotes(root, chunks)
            setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
            await build_index(setup, root)
            for item in items:
                off = await _answer(setup, item, tracing=False)
                on = await _answer(setup, item, tracing=True)
                if off != on:
                    mismatches += 1
                    print(f"mismatch {item.id}", file=sys.stderr)
                    print(f"  off={off}", file=sys.stderr)
                    print(f"  on={on}", file=sys.stderr)
    finally:
        reset_for_tests()
        await client.close()
    return mismatches


async def _answer(setup: RetrievalSetup, item: GoldenItem, *, tracing: bool) -> dict[str, object]:
    if tracing:
        configure_for_tests(MemoryExporter())
    else:
        reset_for_tests()
    hits = await retrieve(
        setup,
        item.question,
        mode=RetrievalMode.HYBRID,
        k=8,
    )
    answer = await ask(hits, item.question, FakeStructuredModel(), min_score=0.0)
    return _banker(answer)


def _banker(answer: Answered | Refused) -> dict[str, object]:
    if isinstance(answer, Answered):
        return {
            "kind": answer.kind,
            "answer": answer.answer,
            "refusal_reason": None,
            "citations": [
                {
                    "doc_id": citation.doc_id,
                    "start": citation.start,
                    "end": citation.end,
                    "quote": citation.quote,
                }
                for citation in answer.citations
            ],
        }
    return {
        "kind": answer.kind,
        "answer": None,
        "refusal_reason": answer.refusal_reason,
        "citations": [],
    }


def _chunks(items: list[GoldenItem]) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[tuple[str, int, int]] = set()
    synthetic = 1_000_000_000
    for item in items:
        for row in item.evidence:
            start, end = _span(row, synthetic)
            if row.start is None:
                synthetic = end + 1
            key = (row.doc_id, start, end)
            if key in seen:
                continue
            seen.add(key)
            chunks.append(_chunk(row, start, end))
    return chunks


def _span(row: Evidence, synthetic: int) -> tuple[int, int]:
    if row.start is not None and row.end is not None:
        return row.start, row.end
    return synthetic, synthetic + len(row.quote)


def _chunk(row: Evidence, start: int, end: int) -> Chunk:
    return Chunk(
        doc_id=row.doc_id,
        section=row.section,
        start=start,
        end=end,
        offset=start,
        source_url="https://www.sec.gov/Archives/edgar/data/quote-index",
        text=row.quote,
        token_count=count_tokens(row.quote),
        chunker=ChunkerName.SECTION,
        ticker="",
        form="",
        period="",
        accession=row.doc_id,
    )


def _write_quotes(root: Path, chunks: list[Chunk]) -> None:
    folder = root / "parsed" / "edgar" / "Q" / "10-K" / "2024-12-31" / "quotes"
    folder.mkdir(parents=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
