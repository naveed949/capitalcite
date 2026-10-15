"""Walk an ingest tree, parse, chunk, and write derived files atomically."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from capitalcite.atomic import atomic_write_text
from capitalcite.chunking import chunk_document
from capitalcite.chunking.models import Chunk, ChunkerName, ChunkStats
from capitalcite.ingest.models import FilingMetadata
from capitalcite.ingest.store import FilingStore
from capitalcite.parse import CitationSource, parse_path

logger = logging.getLogger(__name__)


@dataclass
class ChunkRunSummary:
    """Totals from one ``capitalcite chunk`` run."""

    files: int = 0
    chunks: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)
    stats: list[ChunkStats] = field(default_factory=list)

    @property
    def mean_tokens(self) -> float:
        total = sum(item.total_tokens for item in self.stats)
        count = sum(item.count for item in self.stats)
        if count == 0:
            return 0.0
        return total / count


def iter_raw_filings(root: Path) -> Iterator[tuple[FilingMetadata, Path]]:
    """Yield stored metadata plus the sibling primary document."""
    base = root / "raw" / "edgar"
    if not base.is_dir():
        return
    for meta_path in sorted(base.rglob("metadata.json")):
        metadata = FilingMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
        document = meta_path.with_name(Path(metadata.primary_document).name)
        yield metadata, document


def run_chunk(
    *,
    input_root: Path,
    output: FilingStore,
    chunker: ChunkerName,
    size: int,
    overlap: int,
) -> ChunkRunSummary:
    """Parse every stored filing under ``input_root`` and write parsed output."""
    summary = ChunkRunSummary()
    for metadata, path in iter_raw_filings(input_root):
        if not path.is_file():
            summary.failed.append((path, "missing primary document"))
            continue
        source = CitationSource(
            doc_id=metadata.accession,
            source_url=metadata.url,
            ticker=metadata.ticker,
            form=metadata.form,
            period=metadata.period,
            accession=metadata.accession,
        )
        try:
            parsed = parse_path(path, source)
            chunks, stats = chunk_document(parsed, chunker=chunker, size=size, overlap=overlap)
            _write_parsed(output, metadata, parsed.text, chunks, stats)
        except Exception as exc:
            logger.exception("chunk failed %s", path)
            summary.failed.append((path, str(exc)))
            continue
        summary.files += 1
        summary.chunks += stats.count
        summary.stats.append(stats)
    logger.info(
        "chunk strategy=%s files=%s chunks=%s mean_tokens=%.1f",
        chunker,
        summary.files,
        summary.chunks,
        summary.mean_tokens,
    )
    return summary


def _write_parsed(
    store: FilingStore,
    metadata: FilingMetadata,
    text: str,
    chunks: list[Chunk],
    stats: ChunkStats,
) -> None:
    store.parsed_dir(metadata).mkdir(parents=True, exist_ok=True)
    document = text if text.endswith("\n") else f"{text}\n"
    atomic_write_text(store.document_md_path(metadata), document)
    lines = [chunk.model_dump_json() for chunk in chunks]
    payload = "\n".join(lines) + ("\n" if lines else "")
    atomic_write_text(store.chunks_path(metadata), payload)
    atomic_write_text(
        store.chunk_stats_path(metadata),
        stats.model_dump_json(indent=2) + "\n",
    )
