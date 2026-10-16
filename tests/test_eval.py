from __future__ import annotations

import json
import math
from pathlib import Path

from typer.testing import CliRunner

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.cli import app
from capitalcite.eval.judge import relevant_ranks, score_item
from capitalcite.eval.models import AggregateScore, EvalReport, ItemScore, dcg, idcg
from capitalcite.eval.report import BEGIN_MARK, END_MARK, render_markdown, update_readme
from capitalcite.golden.models import Evidence, EvidenceRole, GoldenItem, ItemSource, QuestionType
from capitalcite.retrieval.types import Hit, RetrievalMode

runner = CliRunner()

_DOC = "0000019617-25-000270"
_URL = "https://www.sec.gov/Archives/edgar/data/19617/jpm.htm"


def test_ndcg_rank_2_is_one_over_log2_3() -> None:
    score = ItemScore(item_id="example-rank-2", gold_spans=1, covered_at=(2,))
    assert score.hit_at(5) is True
    assert score.mrr_at(5) == 0.5
    assert score.ndcg_at(10) == 1 / math.log2(3)
    assert score.ndcg_at(1) == 0.0


def test_miss_beyond_k_is_zero() -> None:
    score = ItemScore(item_id="example-rank-8", gold_spans=1, covered_at=(8,))
    assert score.hit_at(5) is False
    assert score.mrr_at(5) == 0.0
    assert score.mrr_at(10) == 0.125
    assert score.ndcg_at(10) == 1 / math.log2(9)


def test_aggregate_means_are_macro() -> None:
    totals = AggregateScore.from_items(
        (
            ItemScore(item_id="a", gold_spans=1, covered_at=(1,)),
            ItemScore(item_id="b", gold_spans=1, covered_at=(8,)),
            ItemScore(item_id="c", gold_spans=1, covered_at=()),
        )
    )
    assert totals.items == 3
    assert totals.recall_at_5 == 1 / 3
    assert totals.recall_at_10 == 2 / 3
    assert totals.mrr_at_10 == (1.0 + 0.125 + 0.0) / 3
    assert totals.misses_at_5 == ("b", "c")
    assert totals.ndcg_at_5 == (1.0 + 0.0 + 0.0) / 3


def test_binary_ndcg_one_hit_covering_two_spans() -> None:
    score = ItemScore(item_id="two-gold", gold_spans=2, covered_at=(1,))
    assert score.ndcg_at(5) == dcg((1,), 5) / idcg(2)
    assert idcg(2) == 1.0 + 1 / math.log2(3)


def test_relevant_ranks_use_span_overlap() -> None:
    item = _item()
    hits = [
        Hit(score=1.0, chunk=_chunk(doc_id="other", start=0, end=10)),
        Hit(score=0.5, chunk=_chunk(doc_id=_DOC, start=487800, end=487900)),
    ]
    assert relevant_ranks(hits, item.evidence) == (2,)
    scored = score_item(item, hits)
    assert scored.covered_at == (2,)
    assert scored.first_rank == 2


def test_cli_help_lists_eval() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "eval" in result.stdout


def test_eval_help_lists_small_and_suite() -> None:
    result = runner.invoke(app, ["eval", "--help"])
    assert result.exit_code == 0
    assert "--suite" in result.stdout
    assert "--small" in result.stdout
    assert "--from-results" in result.stdout


def test_small_rejects_embedder() -> None:
    result = runner.invoke(
        app,
        ["eval", "--suite", "golden", "--small", "--embedder", "bge-small"],
    )
    assert result.exit_code == 1
    assert "--small does not take --embedder" in result.output


def test_from_results_prints_table(tmp_path: Path) -> None:
    path = tmp_path / "matrix.json"
    path.write_text(_sample_report_json(), encoding="utf-8")
    result = runner.invoke(app, ["eval", "--suite", "golden", "--from-results", str(path)])
    assert result.exit_code == 0
    assert "Recall@5" in result.stdout
    assert "0.500" in result.stdout
    assert "cc-h-013" in result.stdout


def test_update_readme_replaces_markers(tmp_path: Path) -> None:
    from capitalcite.eval.report import load_report

    json_path = tmp_path / "matrix.json"
    json_path.write_text(_sample_report_json(), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        f"before\n{BEGIN_MARK}\nOLD\n{END_MARK}\nafter\n",
        encoding="utf-8",
    )
    update_readme(readme, load_report(json_path))
    text = readme.read_text(encoding="utf-8")
    assert "OLD" not in text
    assert "0.500" in text
    assert text.startswith("before\n")
    assert text.endswith("after\n")


def test_small_bm25_writes_json(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "golden",
            "--small",
            "--chunker",
            "section",
            "--mode",
            "bm25",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Recall@5" in result.stdout
    files = list(tmp_path.glob("*-small.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding="utf-8"))
    assert body["small"] is True
    assert body["n_items"] >= 1
    assert body["arms"][0]["config_hash"]
    assert body["arms"][0]["mode"] == "bm25"


def test_render_markdown_names_probe() -> None:
    report = load_report_from_text(_sample_report_json())
    table = render_markdown(report)
    assert "cc-h-013 rank 8" in table
    assert "config_hash" in table


def load_report_from_text(raw: str) -> EvalReport:
    return EvalReport.model_validate_json(raw)


def _item() -> GoldenItem:
    return GoldenItem(
        id="cc-h-013",
        question="What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?",
        answer="15.7%",
        type=QuestionType.NUMERIC,
        answerable=True,
        source=ItemSource.HANDWRITTEN,
        evidence=[
            Evidence(
                doc_id=_DOC,
                section="Item 7",
                quote="15.7%",
                role=EvidenceRole.ANSWER,
                start=487805,
                end=487867,
            )
        ],
    )


def _chunk(*, doc_id: str, start: int, end: int) -> Chunk:
    return Chunk(
        doc_id=doc_id,
        section="Item 7",
        start=start,
        end=end,
        offset=start,
        source_url=_URL,
        text="x" * max(end - start, 1),
        token_count=4,
        chunker=ChunkerName.SECTION,
        ticker="JPM",
        form="10-K",
        period="2024-12-31",
        accession=doc_id,
    )


def _sample_report_json() -> str:
    from capitalcite.eval.models import ArmResult, EvalReport

    scores = (
        ItemScore(item_id="cc-h-001", gold_spans=1, covered_at=(1,)),
        ItemScore(item_id="cc-h-013", gold_spans=1, covered_at=(8,)),
    )
    arm = ArmResult(
        config_hash="a" * 64,
        collection_name="filings-" + "a" * 64,
        embedder_id="fastembed:BAAI/bge-small-en-v1.5",
        chunker=ChunkerName.SECTION,
        chunk_size=512,
        chunk_overlap=64,
        mode=RetrievalMode.HYBRID,
        k=5,
        rrf_k=60,
        point_count=12,
        metrics=AggregateScore.from_items(scores),
        item_scores=scores,
    )
    report = EvalReport(
        git_sha="b" * 40,
        suite="golden",
        small=False,
        k=5,
        n_items=2,
        embedder_id=arm.embedder_id,
        arms=(arm,),
    )
    return report.model_dump_json()
