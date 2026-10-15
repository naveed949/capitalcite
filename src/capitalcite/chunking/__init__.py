"""Pick a named chunker and attach citation metadata."""

from __future__ import annotations

import logging

from capitalcite.chunking.models import Chunk, ChunkerName, ChunkStats
from capitalcite.chunking.strategies import CHUNKERS
from capitalcite.parse.models import ParsedDocument

logger = logging.getLogger(__name__)

__all__ = [
    "CHUNKERS",
    "Chunk",
    "ChunkStats",
    "ChunkerName",
    "chunk_document",
]


def chunk_document(
    document: ParsedDocument,
    *,
    chunker: ChunkerName,
    size: int,
    overlap: int,
) -> tuple[list[Chunk], ChunkStats]:
    """Run the named strategy and log count plus mean tokens."""
    try:
        fn = CHUNKERS[chunker]
    except KeyError as exc:
        raise ValueError(f"unknown chunker {chunker!r}") from exc
    chunks = fn(document, size=size, overlap=overlap)
    stats = ChunkStats.from_chunks(chunker, chunks)
    logger.info(
        "chunker=%s count=%s mean_tokens=%.1f total_tokens=%s doc_id=%s",
        stats.chunker,
        stats.count,
        stats.mean_tokens,
        stats.total_tokens,
        document.source.doc_id,
    )
    return chunks, stats
