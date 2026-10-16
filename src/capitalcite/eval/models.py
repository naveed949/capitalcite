"""Records for one retrieval-eval arm and the matrix they form."""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, computed_field

from capitalcite.chunking.models import ChunkerName
from capitalcite.retrieval.types import RetrievalMode


class ItemScore(BaseModel):
    """One golden item after retrieve(). covered_at is 1-based."""

    model_config = ConfigDict(frozen=True)
    item_id: str = Field(min_length=1)
    gold_spans: int = Field(ge=1)
    covered_at: tuple[int, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_rank(self) -> int | None:
        return self.covered_at[0] if self.covered_at else None

    def hit_at(self, k: int) -> bool:
        if k < 1:
            raise ValueError("k must be at least 1")
        return any(rank <= k for rank in self.covered_at)

    def mrr_at(self, k: int) -> float:
        if k < 1:
            raise ValueError("k must be at least 1")
        for rank in self.covered_at:
            if rank <= k:
                return 1.0 / rank
        return 0.0

    def ndcg_at(self, k: int) -> float:
        if k < 1:
            raise ValueError("k must be at least 1")
        credited = self.covered_at[: self.gold_spans]
        ideal = idcg(min(k, self.gold_spans))
        if ideal == 0.0:
            return 0.0
        return min(1.0, dcg(credited, k) / ideal)


class AggregateScore(BaseModel):
    """Macro means over ItemScore. Built only by from_items."""

    model_config = ConfigDict(frozen=True)
    items: int = Field(ge=0)
    recall_at_5: float
    recall_at_10: float
    mrr_at_5: float
    mrr_at_10: float
    ndcg_at_5: float
    ndcg_at_10: float
    misses_at_5: tuple[str, ...]

    @classmethod
    def from_items(cls, scores: Sequence[ItemScore]) -> AggregateScore:
        rows = tuple(scores)
        n = len(rows)
        if n == 0:
            return cls(
                items=0,
                recall_at_5=0.0,
                recall_at_10=0.0,
                mrr_at_5=0.0,
                mrr_at_10=0.0,
                ndcg_at_5=0.0,
                ndcg_at_10=0.0,
                misses_at_5=(),
            )
        return cls(
            items=n,
            recall_at_5=sum(row.hit_at(5) for row in rows) / n,
            recall_at_10=sum(row.hit_at(10) for row in rows) / n,
            mrr_at_5=sum(row.mrr_at(5) for row in rows) / n,
            mrr_at_10=sum(row.mrr_at(10) for row in rows) / n,
            ndcg_at_5=sum(row.ndcg_at(5) for row in rows) / n,
            ndcg_at_10=sum(row.ndcg_at(10) for row in rows) / n,
            misses_at_5=tuple(row.item_id for row in rows if not row.hit_at(5)),
        )


class ArmResult(BaseModel):
    """One (chunker, embedder, mode) cell. config_hash is IndexConfig."""

    model_config = ConfigDict(frozen=True)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_name: str
    embedder_id: str
    chunker: ChunkerName
    chunk_size: int = Field(ge=1)
    chunk_overlap: int = Field(ge=0)
    mode: RetrievalMode
    k: int = Field(ge=1)
    rrf_k: int = Field(ge=1)
    point_count: int = Field(ge=0)
    metrics: AggregateScore
    item_scores: tuple[ItemScore, ...]


class AgentVsRag(BaseModel):
    """Retrieval recall on comparison and multi-hop items. Not answer quality."""

    model_config = ConfigDict(frozen=True)
    label: str = "fake/heuristic planner"
    n: int = Field(ge=0)
    chunker: ChunkerName
    mode: RetrievalMode
    rag_recall_at_5: float
    rag_recall_at_10: float
    agent_recall_at_5: float
    agent_recall_at_10: float
    item_ids: tuple[str, ...] = ()


class EvalReport(BaseModel):
    """Whole matrix. Written to evals/results/<sha12>.json."""

    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    git_sha: str
    suite: str
    small: bool
    k: int = Field(ge=1)
    n_items: int = Field(ge=0)
    embedder_id: str
    sparse_id: str = ""
    fastembed_version: str = ""
    qdrant_client_version: str = ""
    arms: tuple[ArmResult, ...]
    agent_vs_rag: AgentVsRag | None = None


def dcg(ranks: tuple[int, ...], k: int) -> float:
    """Binary DCG. Each rank in 1..k contributes 1 / log2(rank + 1)."""
    return sum(1.0 / math.log2(rank + 1) for rank in ranks if 1 <= rank <= k)


def idcg(n: int) -> float:
    """Ideal DCG for n relevant documents placed at ranks 1..n."""
    if n < 1:
        return 0.0
    return sum(1.0 / math.log2(index + 2) for index in range(n))
