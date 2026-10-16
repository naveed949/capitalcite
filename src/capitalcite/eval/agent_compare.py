"""Agent vs RAG retrieval recall on comparison and multi-hop items."""

from __future__ import annotations

from collections.abc import Sequence

from capitalcite.ask.guard import tickers_named, years_named
from capitalcite.chunking.models import ChunkerName
from capitalcite.eval.judge import score_item
from capitalcite.eval.models import AgentVsRag, AggregateScore
from capitalcite.golden.models import GoldenItem, QuestionType
from capitalcite.retrieval import RetrievalMode, RetrievalSetup, retrieve
from capitalcite.retrieval.types import Hit, parse_filters

_HOP_TYPES = frozenset({QuestionType.COMPARISON, QuestionType.MULTI_HOP})
_AGENT_LABEL = "fake/heuristic planner"


def hop_items(items: Sequence[GoldenItem]) -> tuple[GoldenItem, ...]:
    """Comparison and multi-hop rows that already have recall spans."""
    return tuple(item for item in items if item.type in _HOP_TYPES)


async def agent_search_hits(
    setup: RetrievalSetup,
    question: str,
    *,
    mode: RetrievalMode,
    k: int,
) -> list[Hit]:
    """Search each named ticker and year, same as FakeAgentModel."""
    tickers: list[str | None] = []
    tickers.extend(sorted(tickers_named(question)))
    if not tickers:
        tickers.append(None)
    years: list[str | None] = []
    years.extend(str(year) for year in sorted(years_named(question)))
    if not years:
        years.append(None)
    hits: list[Hit] = []
    seen: set[tuple[str, int, int]] = set()
    for ticker in tickers:
        for year in years:
            filters = parse_filters(ticker=ticker, form=None, period=year)
            batch = await retrieve(setup, question, mode=mode, k=k, filters=filters)
            for hit in batch:
                key = (hit.chunk.doc_id, hit.chunk.start, hit.chunk.end)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(hit)
    return hits


async def compare_agent_vs_rag(
    setup: RetrievalSetup,
    items: Sequence[GoldenItem],
    *,
    chunker: ChunkerName,
    mode: RetrievalMode,
    retrieve_k: int,
) -> AgentVsRag:
    """Score one-shot retrieve against the fake planner's filtered searches."""
    rag_scores = []
    agent_scores = []
    ids: list[str] = []
    for item in items:
        rag_hits = await retrieve(setup, item.question, mode=mode, k=retrieve_k)
        planner_hits = await agent_search_hits(setup, item.question, mode=mode, k=retrieve_k)
        rag_scores.append(score_item(item, rag_hits))
        agent_scores.append(score_item(item, planner_hits))
        ids.append(item.id)
    rag = AggregateScore.from_items(rag_scores)
    agent = AggregateScore.from_items(agent_scores)
    return AgentVsRag(
        label=_AGENT_LABEL,
        n=len(ids),
        chunker=chunker,
        mode=mode,
        rag_recall_at_5=rag.recall_at_5,
        rag_recall_at_10=rag.recall_at_10,
        agent_recall_at_5=agent.recall_at_5,
        agent_recall_at_10=agent.recall_at_10,
        item_ids=tuple(ids),
    )
