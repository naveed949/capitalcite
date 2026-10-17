"""Stable identity for the persisted eval Qdrant cache.

The GitHub Actions key is SHA-256 of the IndexConfig hash(es) plus the
sorted EDGAR accession ids. A chunker, embedder, chunk size, or filing
change is a cache miss. Computing this does not load FastEmbed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import iter_raw_filings
from capitalcite.config import Settings
from capitalcite.retrieval.embedders import spec_for
from capitalcite.retrieval.hashing import config_hash
from capitalcite.retrieval.sparse import FASTEMBED_SPARSE_ID
from capitalcite.retrieval.types import EmbedderName, IndexConfig

# Must match runner._setup. Env QDRANT_COLLECTION is not part of the eval store.
EVAL_COLLECTION_PREFIX = "filings"


def accession_ids(input_dir: Path) -> tuple[str, ...]:
    """Sorted unique accessions from ``raw/edgar/**/metadata.json``."""
    found = {metadata.accession for metadata, _ in iter_raw_filings(input_dir)}
    return tuple(sorted(found))


def eval_index_config(
    settings: Settings,
    chunker: ChunkerName,
    embedder: EmbedderName,
) -> IndexConfig:
    """IndexConfig the nightly eval will build, without constructing an embedder."""
    spec = spec_for(embedder)
    return IndexConfig(
        embedder_id=spec.embedder_id,
        dense_size=spec.dense_size,
        sparse_id=FASTEMBED_SPARSE_ID,
        chunker=chunker,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        collection_prefix=EVAL_COLLECTION_PREFIX,
    )


def eval_index_cache_key(
    input_dir: Path,
    *,
    chunkers: Sequence[ChunkerName],
    embedder: EmbedderName,
    settings: Settings,
) -> str:
    """SHA-256 hex of config hashes plus sorted accession ids."""
    ids = accession_ids(input_dir)
    if not ids:
        raise ValueError(f"no metadata.json under {input_dir / 'raw' / 'edgar'}")
    if not chunkers:
        raise ValueError("need at least one chunker")
    payload = {
        "accessions": list(ids),
        "configs": [
            config_hash(eval_index_config(settings, chunker, embedder)) for chunker in chunkers
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
