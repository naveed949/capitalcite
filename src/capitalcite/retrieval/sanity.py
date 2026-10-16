"""Quote-index recall sanity. Not the 12-filing corpus eval."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens
from capitalcite.config import Settings
from capitalcite.golden.models import Evidence, GoldenItem
from capitalcite.golden.store import ValidationError, load_golden_set
from capitalcite.retrieval.embedders import FakeEmbedder
from capitalcite.retrieval.service import build_index, retrieve, setup_retrieval
from capitalcite.retrieval.types import Hit, RetrievalMode


class SanityUnavailable(Exception):
    """The handwritten golden file could not be loaded."""


class QuoteSanity(BaseModel):
    model_config = ConfigDict(frozen=True)
    recall_at_5: float
    hits: int
    items: int
    hit_ids: tuple[str, ...]
    mode: str = "hybrid"


def span_overlap(chunk: Chunk, evidence: Evidence) -> bool:
    """True when doc_id matches and the half-open spans overlap."""
    if evidence.start is None or evidence.end is None:
        return False
    if chunk.doc_id != evidence.doc_id:
        return False
    return chunk.start < evidence.end and evidence.start < chunk.end


async def run_quote_index_sanity(golden_dir: Path = Path("evals/golden")) -> QuoteSanity:
    """Index each handwritten quote as its own chunk and retrieve the question.

    The number is lexical overlap between the question and the gold quote.
    It is not recall over the parsed 12-filing corpus. Issue #6 owns that matrix.
    """
    try:
        golden = load_golden_set(golden_dir)
    except (ValidationError, OSError) as exc:
        raise SanityUnavailable(str(exc)) from exc
    items = [
        item
        for item in golden.items
        if item.in_recall and any(_spanned(row) for row in item.evidence)
    ]
    if not items:
        raise SanityUnavailable("no handwritten evidence spans")
    chunks = [_quote_chunk(row) for item in items for row in item.evidence if _spanned(row)]
    settings = Settings(
        sec_user_agent="capitalcite-sanity sanity@example.com",
        chunker=ChunkerName.SECTION,
        chunk_size=512,
        chunk_overlap=64,
        qdrant_collection="sanity",
        embedder="bge-small",
        search_k=5,
        rrf_k=60,
    )
    client = AsyncQdrantClient(":memory:")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_quotes(root, chunks)
            setup = setup_retrieval(settings, client=client, embedder=FakeEmbedder())
            await build_index(setup, root)
            hit_ids: list[str] = []
            for item in items:
                found = await retrieve(
                    setup,
                    item.question,
                    mode=RetrievalMode.HYBRID,
                    k=5,
                )
                if _item_hit(item, found):
                    hit_ids.append(item.id)
    finally:
        await client.close()
    total = len(items)
    return QuoteSanity(
        recall_at_5=len(hit_ids) / total,
        hits=len(hit_ids),
        items=total,
        hit_ids=tuple(hit_ids),
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    golden_dir = Path(args[0]) if args else Path("evals/golden")
    try:
        report = asyncio.run(run_quote_index_sanity(golden_dir))
    except SanityUnavailable as exc:
        print(f"quote-index sanity unverified: {exc}", file=sys.stderr)
        return 2
    print(
        "quote-index sanity "
        f"recall_at_5={report.recall_at_5:.3f} "
        f"hits={report.hits} "
        f"items={report.items} "
        f"mode={report.mode} "
        "label=sanity"
    )
    print(
        "measured hybrid rank of each handwritten question against its gold quote, "
        "indexed as its own chunk, using FakeEmbedder dense vectors and BM25. "
        "Quotes that share the question's words compete for the five slots. "
        "This is not corpus recall over the 12 filings."
    )
    return 0


def _spanned(row: Evidence) -> bool:
    return row.start is not None and row.end is not None


def _item_hit(item: GoldenItem, found: list[Hit]) -> bool:
    return any(span_overlap(hit.chunk, row) for hit in found for row in item.evidence)


def _quote_chunk(row: Evidence) -> Chunk:
    start = row.start if row.start is not None else 0
    end = row.end if row.end is not None else start
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
