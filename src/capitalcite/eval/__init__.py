"""Corpus retrieval eval. Quote-index sanity lives in retrieval.sanity."""

from capitalcite.eval.baseline import (
    DEFAULT_NIGHTLY,
    Baseline,
    CompareReport,
    NightlyArm,
    NightlyBaseline,
    NightlyThresholds,
    Thresholds,
    TrackedRate,
    compare,
    compare_nightly,
    snapshot_nightly,
)
from capitalcite.eval.check import DEFAULT_BASELINE, CheckFailed, CheckReport, run_check
from capitalcite.eval.diagnose import diagnose_bm25, rare_token_examples
from capitalcite.eval.judge import recall_items, relevant_ranks, score_item
from capitalcite.eval.models import AggregateScore, ArmResult, EvalReport, ItemScore, dcg, idcg
from capitalcite.eval.report import render_markdown, write_report
from capitalcite.eval.runner import EvalFailed, EvalUnverified, run_suite

__all__ = [
    "DEFAULT_BASELINE",
    "DEFAULT_NIGHTLY",
    "AggregateScore",
    "ArmResult",
    "Baseline",
    "CheckFailed",
    "CheckReport",
    "CompareReport",
    "EvalFailed",
    "EvalReport",
    "EvalUnverified",
    "ItemScore",
    "NightlyArm",
    "NightlyBaseline",
    "NightlyThresholds",
    "Thresholds",
    "TrackedRate",
    "compare",
    "compare_nightly",
    "dcg",
    "diagnose_bm25",
    "idcg",
    "rare_token_examples",
    "recall_items",
    "relevant_ranks",
    "render_markdown",
    "run_check",
    "run_suite",
    "score_item",
    "snapshot_nightly",
    "write_report",
]
