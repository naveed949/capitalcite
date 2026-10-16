"""Label retrieved hits with span_overlap. No Qdrant."""

from __future__ import annotations

from collections.abc import Sequence

from capitalcite.eval.models import ItemScore
from capitalcite.golden.models import Evidence, GoldenItem
from capitalcite.retrieval.sanity import span_overlap
from capitalcite.retrieval.types import Hit


def recall_items(items: Sequence[GoldenItem]) -> tuple[GoldenItem, ...]:
    """Handwritten items that carry at least one start/end pair."""
    return tuple(
        item for item in items if item.in_recall and any(_spanned(row) for row in item.evidence)
    )


def relevant_ranks(hits: Sequence[Hit], evidence: Sequence[Evidence]) -> tuple[int, ...]:
    """1-based ranks of hits whose chunk overlaps any spanned evidence row."""
    gold = [row for row in evidence if _spanned(row)]
    ranks: list[int] = []
    for index, hit in enumerate(hits, start=1):
        if any(span_overlap(hit.chunk, row) for row in gold):
            ranks.append(index)
    return tuple(ranks)


def score_item(item: GoldenItem, hits: Sequence[Hit]) -> ItemScore:
    gold = [row for row in item.evidence if _spanned(row)]
    if not gold:
        raise ValueError(f"{item.id}: no spanned evidence")
    return ItemScore(
        item_id=item.id,
        gold_spans=len(gold),
        covered_at=relevant_ranks(hits, item.evidence),
    )


def _spanned(row: Evidence) -> bool:
    return row.start is not None and row.end is not None
