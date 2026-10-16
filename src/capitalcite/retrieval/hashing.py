"""Canonical bytes for an index, point ids, sparse counts, and the paper RRF formula."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from capitalcite.retrieval.types import SparseTermVector

if TYPE_CHECKING:
    from capitalcite.retrieval.types import IndexConfig

# Fixed so a rebuild upserts the same point instead of orphaning the previous one.
_POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/naveed949/capitalcite/point")
_TOKEN = re.compile(r"[a-z0-9]+")
_EXACT_PERIOD = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
_YEAR_PERIOD = re.compile(r"^(\d{4})$")


def config_hash(config: IndexConfig) -> str:
    """SHA-256 hex of canonical JSON, sorted keys, compact separators."""
    payload = config.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def collection_name(config: IndexConfig) -> str:
    """``{prefix}-{config_hash}``."""
    return f"{config.collection_prefix}-{config_hash(config)}"


def point_id(doc_id: str, chunker: str, start: int, end: int) -> str:
    """UUID5 of doc_id, chunker, start, and end. The config hash is not an input."""
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{doc_id}|{chunker}|{start}|{end}"))


def year_of(period: str) -> int | None:
    """Fiscal year from YYYY-MM-DD or a 4-digit year. Anything else is None."""
    match = _EXACT_PERIOD.fullmatch(period) or _YEAR_PERIOD.fullmatch(period)
    if match is None:
        return None
    year = int(match.group(1))
    if 1900 <= year <= 2100:
        return year
    return None


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def term_counts(text: str) -> SparseTermVector:
    """Lowercase word tokens. The index is blake2s truncated to 32 bits.

    Values are raw counts. Indices are unique and sorted. Truncation avoids a
    vocabulary file that can drift from the points already stored. Two tokens
    can share an index.
    """
    counts: dict[int, int] = {}
    for token in tokens(text):
        digest = hashlib.blake2s(token.encode("utf-8"), digest_size=4).digest()
        index = int.from_bytes(digest, "big")
        counts[index] = counts.get(index, 0) + 1
    indices = tuple(sorted(counts))
    values = tuple(float(counts[index]) for index in indices)
    return SparseTermVector(indices=indices, values=values)


def reciprocal_rank_fusion(
    rank_lists: Sequence[Sequence[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Equal-weight reciprocal rank fusion from the Cormack paper.

    Rank is 1-based. A missing id adds nothing. Score is the sum of
    ``1 / (k + rank)``. Sort by score descending, then id ascending.

    Qdrant RrfQuery uses a zero-based rank and is not this function.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    scores: dict[str, float] = {}
    for ranking in rank_lists:
        seen: set[str] = set()
        for rank, item_id in enumerate(ranking, start=1):
            if item_id in seen:
                raise ValueError(f"duplicate id {item_id}")
            seen.add(item_id)
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
