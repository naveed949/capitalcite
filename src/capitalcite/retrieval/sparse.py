"""BM25 sparse vectors. Production uses FastEmbed Qdrant/bm25. Tests use this formula."""

from __future__ import annotations

import hashlib
from collections import Counter

from capitalcite.retrieval.hashing import tokens
from capitalcite.retrieval.types import SparseTermVector

BM25_K1 = 1.2
BM25_B = 0.75
BM25_AVGDL = 256.0
SPARSE_MODEL = "Qdrant/bm25"
FASTEMBED_SPARSE_ID = f"fastembed:{SPARSE_MODEL}:k1={BM25_K1}:b={BM25_B}:avgdl={BM25_AVGDL}"
FAKE_SPARSE_ID = f"fake:bm25:k1={BM25_K1}:b={BM25_B}:avgdl={BM25_AVGDL}"

# Compact English list so FakeEmbedder drops the same class of query words as
# FastEmbed Qdrant/bm25. Production stopwords come from that model's files.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "after",
        "again",
        "all",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "no",
        "not",
        "now",
        "of",
        "on",
        "once",
        "or",
        "other",
        "our",
        "over",
        "s",
        "same",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "under",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "will",
        "with",
        "you",
        "your",
    }
)


def bm25_terms(text: str) -> list[str]:
    """Lowercased word tokens with stopwords and single letters removed."""
    return [tok for tok in tokens(text) if tok not in _STOPWORDS and len(tok) > 1]


def bm25_document(
    text: str,
    *,
    k1: float = BM25_K1,
    b: float = BM25_B,
    avgdl: float = BM25_AVGDL,
) -> SparseTermVector:
    """Document weights: tf * (k1+1) / (tf + k1 * (1 - b + b * dl/avgdl))."""
    terms = bm25_terms(text)
    counts = Counter(terms)
    doc_len = len(terms)
    weights: dict[int, float] = {}
    for token, tf in counts.items():
        denom = tf + k1 * (1.0 - b + b * doc_len / avgdl)
        weights[_index(token)] = tf * (k1 + 1.0) / denom
    indices = tuple(sorted(weights))
    return SparseTermVector(indices=indices, values=tuple(weights[index] for index in indices))


def bm25_query(text: str) -> SparseTermVector:
    """Query weights: 1.0 per unique remaining term. Qdrant IDF supplies IDF."""
    unique = list(dict.fromkeys(bm25_terms(text)))
    weights = {_index(token): 1.0 for token in unique}
    indices = tuple(sorted(weights))
    return SparseTermVector(indices=indices, values=tuple(1.0 for _ in indices))


def _index(token: str) -> int:
    digest = hashlib.blake2s(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")
