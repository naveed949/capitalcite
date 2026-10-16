"""Hybrid retrieval over chunks already written to disk."""

from capitalcite.retrieval.embedders import FakeEmbedder
from capitalcite.retrieval.hashing import (
    collection_name,
    config_hash,
    point_id,
    reciprocal_rank_fusion,
    term_counts,
    year_of,
)
from capitalcite.retrieval.sanity import span_overlap
from capitalcite.retrieval.service import (
    RetrievalSetup,
    build_index,
    create_retrieval_setup,
    point_count,
    retrieve,
    setup_retrieval,
)
from capitalcite.retrieval.types import (
    CorruptChunks,
    EmbedderName,
    EmbedderNotConfigured,
    Hit,
    IndexConfig,
    IndexNotBuilt,
    IndexReceipt,
    IndexUnavailable,
    RetrievalError,
    RetrievalMode,
    SearchFilters,
    parse_filters,
)

__all__ = [
    "CorruptChunks",
    "EmbedderName",
    "EmbedderNotConfigured",
    "FakeEmbedder",
    "Hit",
    "IndexConfig",
    "IndexNotBuilt",
    "IndexReceipt",
    "IndexUnavailable",
    "RetrievalError",
    "RetrievalMode",
    "RetrievalSetup",
    "SearchFilters",
    "build_index",
    "collection_name",
    "config_hash",
    "create_retrieval_setup",
    "parse_filters",
    "point_count",
    "point_id",
    "reciprocal_rank_fusion",
    "retrieve",
    "setup_retrieval",
    "span_overlap",
    "term_counts",
    "year_of",
]
