"""Corpus retrieval eval. Quote-index sanity lives in retrieval.sanity."""

from capitalcite.eval.diagnose import diagnose_bm25, rare_token_examples
from capitalcite.eval.judge import recall_items, relevant_ranks, score_item
from capitalcite.eval.models import AggregateScore, ArmResult, EvalReport, ItemScore, dcg, idcg
from capitalcite.eval.report import render_markdown, write_report
from capitalcite.eval.runner import EvalFailed, EvalUnverified, run_suite

__all__ = [
    "AggregateScore",
    "ArmResult",
    "EvalFailed",
    "EvalReport",
    "EvalUnverified",
    "ItemScore",
    "dcg",
    "diagnose_bm25",
    "idcg",
    "rare_token_examples",
    "recall_items",
    "relevant_ranks",
    "render_markdown",
    "run_suite",
    "score_item",
    "write_report",
]
