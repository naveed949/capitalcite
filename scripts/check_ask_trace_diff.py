from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from capitalcite.agent.types import AskPath
from capitalcite.api.ask_models import AnsweredEvent, AskRequest, RefusedEvent
from capitalcite.api.routes.ask import iter_ask
from capitalcite.ask.guard import tickers_named, years_named
from capitalcite.ask.models import FakeStructuredModel
from capitalcite.ask.service import ask
from capitalcite.ask.types import Answered, Refused
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens
from capitalcite.config import Settings
from capitalcite.golden.models import Evidence, GoldenItem, QuestionType
from capitalcite.golden.store import load_items
from capitalcite.retrieval.embedders import FakeEmbedder
from capitalcite.retrieval.service import RetrievalSetup, build_index, retrieve, setup_retrieval
from capitalcite.retrieval.types import RetrievalMode
from capitalcite.trace import MemoryExporter, configure_for_tests, reset_for_tests

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "evals" / "golden" / "v1.jsonl"
EXPECTED = 112
MIN_ANSWERED = 8
_CIK_TICKER = {
    "0000019617": "JPM",
    "0001628280": "JPM",
    "0000070858": "BAC",
    "0000831001": "C",
    "0000072971": "WFC",
}
NON_REFUSAL_IDS = (
    "cc-h-001",
    "cc-h-002",
    "cc-h-003",
    "cc-h-013",
    "cc-h-014",
    "cc-h-015",
    "cc-h-016",
    "cc-h-055",
    "cc-h-056",
    "cc-h-061",
)


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
        mismatches, answered = asyncio.run(_compare(items))
    except Exception as exc:
        print(f"ask-trace-diff failed: {exc}", file=sys.stderr)
        return 1
    print(f"ask-trace-diff mismatches={mismatches}")
    print(f"ask-trace-diff answered={answered}")
    if answered < MIN_ANSWERED:
        print(
            f"ask-trace-diff expected at least {MIN_ANSWERED} answered items on the "
            "non-refusal subset",
            file=sys.stderr,
        )
        return 1
    return 0 if mismatches == 0 else 1


async def _compare(items: list[GoldenItem]) -> tuple[int, int]:
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
    answered = 0
    by_id = {item.id: item for item in items}
    extra = [by_id[item_id] for item_id in NON_REFUSAL_IDS if item_id in by_id]
    if len(extra) != len(NON_REFUSAL_IDS):
        missing = [item_id for item_id in NON_REFUSAL_IDS if item_id not in by_id]
        raise RuntimeError(f"missing non-refusal items: {missing}")
    if any(not item.answerable or item.type is QuestionType.UNANSWERABLE for item in extra):
        raise RuntimeError("non-refusal subset contains an unanswerable item")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_quotes(root, chunks)
            setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
            await build_index(setup, root)
            for item in items:
                off = await _answer_rag(setup, item, tracing=False)
                on = await _answer_rag(setup, item, tracing=True)
                if off != on:
                    mismatches += 1
                    _mismatch(item.id, "ask", off, on)
            for item in extra:
                for path in ("rag", "agent"):
                    off = await _answer_iter(setup, settings, item, path=path, tracing=False)
                    on = await _answer_iter(setup, settings, item, path=path, tracing=True)
                    if off != on:
                        mismatches += 1
                        _mismatch(item.id, path, off, on)
                    if path == "rag" and off.get("kind") == "answered":
                        answered += 1
    finally:
        reset_for_tests()
        await client.close()
    return mismatches, answered


def _mismatch(item_id: str, path: str, off: dict[str, object], on: dict[str, object]) -> None:
    print(f"mismatch {item_id} {path}", file=sys.stderr)
    print(f"  off={off}", file=sys.stderr)
    print(f"  on={on}", file=sys.stderr)


async def _answer_rag(
    setup: RetrievalSetup, item: GoldenItem, *, tracing: bool
) -> dict[str, object]:
    _arm_tracer(tracing)
    hits = await retrieve(
        setup,
        item.question,
        mode=RetrievalMode.HYBRID,
        k=8,
    )
    answer = await ask(hits, item.question, FakeStructuredModel(), min_score=0.0)
    return _banker_domain(answer)


async def _answer_iter(
    setup: RetrievalSetup,
    settings: Settings,
    item: GoldenItem,
    *,
    path: AskPath,
    tracing: bool,
) -> dict[str, object]:
    _arm_tracer(tracing)
    body = AskRequest(question=item.question, path=path, model="fake", k=8)
    response = None
    async for event in iter_ask(body, setup, settings):
        if isinstance(event, AnsweredEvent | RefusedEvent):
            response = event.response
    if response is None:
        raise RuntimeError(f"{item.id} {path} produced no terminal event")
    return {
        "kind": response.kind,
        "answer": response.answer,
        "refusal_reason": response.refusal_reason,
        "citations": [
            {
                "doc_id": citation.doc_id,
                "start": citation.start,
                "end": citation.end,
                "quote": citation.quote,
            }
            for citation in response.citations
        ],
        "plan": [(step.tool, dict(step.args), step.status) for step in response.plan],
        "traces": [(step.tool, step.ok) for step in response.traces],
    }


def _arm_tracer(tracing: bool) -> None:
    if tracing:
        configure_for_tests(MemoryExporter())
    else:
        reset_for_tests()


def _banker_domain(answer: Answered | Refused) -> dict[str, object]:
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
            chunks.append(_chunk(row, start, end, item))
    return chunks


def _span(row: Evidence, synthetic: int) -> tuple[int, int]:
    if row.start is not None and row.end is not None:
        return row.start, row.end
    return synthetic, synthetic + len(row.quote)


def _chunk(row: Evidence, start: int, end: int, item: GoldenItem) -> Chunk:
    ticker, period = _quote_identity(row, item)
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
        ticker=ticker,
        form="10-K",
        period=period,
        accession=row.doc_id,
    )


def _quote_identity(row: Evidence, item: GoldenItem) -> tuple[str, str]:
    cik = row.doc_id.split("-")[0]
    ticker = _CIK_TICKER.get(cik, "")
    named_tickers = tickers_named(item.question)
    if not ticker and len(named_tickers) == 1:
        ticker = next(iter(named_tickers))
    years = years_named(item.question)
    period = f"{next(iter(years))}-12-31" if len(years) == 1 else ""
    return ticker, period


def _write_quotes(root: Path, chunks: list[Chunk]) -> None:
    folder = root / "parsed" / "edgar" / "Q" / "10-K" / "2024-12-31" / "quotes"
    folder.mkdir(parents=True)
    lines = [chunk.model_dump_json() for chunk in chunks]
    (folder / "chunks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
