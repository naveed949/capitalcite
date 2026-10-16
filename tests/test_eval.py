from __future__ import annotations

import json
import math
from pathlib import Path

from typer.testing import CliRunner

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.cli import app
from capitalcite.eval.diagnose import rare_token_examples
from capitalcite.eval.judge import relevant_ranks, score_item
from capitalcite.eval.models import AggregateScore, EvalReport, ItemScore, dcg, idcg
from capitalcite.eval.report import (
    BEGIN_MARK,
    END_MARK,
    render_markdown,
    update_readme,
)
from capitalcite.golden.models import Evidence, EvidenceRole, GoldenItem, ItemSource, QuestionType
from capitalcite.retrieval.hashing import tokens
from capitalcite.retrieval.types import Hit, RetrievalMode
from capitalcite.trace import MemoryExporter, ObservationKind, configure_for_tests, reset_for_tests

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


def test_cet1_and_named_rare_tokens_survive() -> None:
    assert tokens("CET1 Wells Fargo SLR 2025") == ["cet1", "wells", "fargo", "slr", "2025"]
    assert rare_token_examples() == ("cet1", "wells", "fargo", "slr", "2025")


def test_ndcg_one_gold_two_overlapping_hits_is_one() -> None:
    item = _item()
    hits = [
        Hit(score=1.0, chunk=_chunk(doc_id=_DOC, start=487800, end=487850)),
        Hit(score=0.9, chunk=_chunk(doc_id=_DOC, start=487840, end=487900)),
    ]
    scored = score_item(item, hits)
    assert scored.gold_spans == 1
    assert scored.covered_at == (1,)
    assert scored.ndcg_at(5) == 1.0
    leftover = ItemScore(item_id="dup", gold_spans=1, covered_at=(1, 2))
    assert leftover.ndcg_at(5) == 1.0
    assert leftover.ndcg_at(5) <= 1.0


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


def test_hop_items_are_comparison_and_multi_hop() -> None:
    from capitalcite.eval.agent_compare import hop_items
    from capitalcite.eval.judge import recall_items
    from capitalcite.golden.store import load_golden_set

    hops = hop_items(recall_items(load_golden_set(Path("evals/golden")).items))
    assert len(hops) == 15
    assert {item.type for item in hops} <= {QuestionType.COMPARISON, QuestionType.MULTI_HOP}


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
    assert "--baseline" in result.stdout
    assert "--write-baseline" in result.stdout


def test_small_rejects_baseline() -> None:
    result = runner.invoke(
        app,
        ["eval", "--suite", "golden", "--small", "--baseline", "evals/baselines/nightly.json"],
    )
    assert result.exit_code == 1
    assert "--small does not take --baseline" in result.output


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


def test_from_results_compare_passes_matching_nightly_snapshot(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    matrix.write_text(_sample_report_json(), encoding="utf-8")
    baseline = tmp_path / "nightly.json"
    written = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "golden",
            "--from-results",
            str(matrix),
            "--write-baseline",
            "--baseline",
            str(baseline),
        ],
    )
    assert written.exit_code == 0, written.output
    assert "wrote baseline" in written.output
    assert "eval-baseline ok=true" in written.output
    compared = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "golden",
            "--from-results",
            str(matrix),
            "--baseline",
            str(baseline),
        ],
    )
    assert compared.exit_code == 0, compared.output
    assert "eval-baseline ok=true" in compared.output


def test_from_results_compare_fails_on_drop(tmp_path: Path) -> None:
    from capitalcite.eval.baseline import load_nightly

    matrix = tmp_path / "matrix.json"
    matrix.write_text(_sample_report_json(), encoding="utf-8")
    baseline = tmp_path / "nightly.json"
    written = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "golden",
            "--from-results",
            str(matrix),
            "--write-baseline",
            "--baseline",
            str(baseline),
        ],
    )
    assert written.exit_code == 0, written.output
    snap = json.loads(baseline.read_text(encoding="utf-8"))
    snap["arms"][0]["recall_at_5"]["hits"] = 3
    baseline.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
    assert load_nightly(baseline).arms[0].recall_at_5.hits == 3
    failed = runner.invoke(
        app,
        [
            "eval",
            "--suite",
            "golden",
            "--from-results",
            str(matrix),
            "--baseline",
            str(baseline),
        ],
    )
    assert failed.exit_code == 1, failed.output
    assert "recall_at_5" in failed.output


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
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    try:
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
        assert "fastembed_version" in body
        assert "qdrant_client_version" in body
        diagnose_files = list(tmp_path.glob("*-bm25-diagnose.md"))
        assert len(diagnose_files) == 1
        text = diagnose_files[0].read_text(encoding="utf-8")
        assert "idf_modifier=IDF" in text
        assert "query_embed" in text
        assert "query_values_all_ones=true" in text
        assert "sparse_id=" in text
        eval_spans = [record for record in exporter.finished() if record.name == "eval"]
        assert len(eval_spans) == 1
        assert eval_spans[0].attributes["capitalcite.git_sha"] == body["git_sha"]
        assert eval_spans[0].attributes["capitalcite.config_hash"] == body["arms"][0]["config_hash"]
        assert eval_spans[0].attributes["capitalcite.suite"] == "golden"
        retrieve_spans = [
            record for record in exporter.finished() if record.kind is ObservationKind.RETRIEVE
        ]
        assert retrieve_spans
        assert retrieve_spans[0].trace_id == eval_spans[0].trace_id
    finally:
        reset_for_tests()


def test_tag_eval_keeps_one_hash_and_drops_a_split() -> None:
    from capitalcite.eval.runner import tag_eval

    report = load_report_from_text(_sample_report_json())
    tagged = tag_eval(report)
    assert tagged["capitalcite.git_sha"] == report.git_sha
    assert tagged["capitalcite.config_hash"] == report.arms[0].config_hash
    other = report.arms[0].model_copy(update={"config_hash": "c" * 64})
    mixed = report.model_copy(update={"arms": (report.arms[0], other)})
    assert "capitalcite.config_hash" not in tag_eval(mixed)


def test_render_markdown_names_probe() -> None:
    report = load_report_from_text(_sample_report_json())
    table = render_markdown(report)
    assert "cc-h-013 rank 8" in table
    assert "config_hash" in table
    assert "credits each gold span once" in table
    assert "Noise floor: n=2, one item is 0.500 recall." in table
    assert "fastembed=absent" in table
    assert "qdrant-client=absent" in table
    assert "sparse=absent" in table
    assert "fake/heuristic planner" not in table


def test_render_markdown_includes_agent_vs_rag() -> None:
    from capitalcite.eval.models import AgentVsRag

    report = load_report_from_text(_sample_report_json())
    filled = report.model_copy(
        update={
            "agent_vs_rag": AgentVsRag(
                n=15,
                chunker=ChunkerName.SECTION,
                mode=RetrievalMode.HYBRID,
                rag_recall_at_5=0.267,
                rag_recall_at_10=0.333,
                agent_recall_at_5=0.4,
                agent_recall_at_10=0.467,
                item_ids=("cc-h-055",),
            )
        }
    )
    table = render_markdown(filled)
    assert "fake/heuristic planner" in table
    assert "Plain RAG Recall@5=0.267" in table
    assert "Agent Recall@5=0.400" in table
    assert "Answer quality stays UNVERIFIED." in table


def test_update_readme_fills_agent_vs_rag_markers(tmp_path: Path) -> None:
    from capitalcite.eval.models import AgentVsRag
    from capitalcite.eval.report import AGENT_BEGIN_MARK, AGENT_END_MARK, load_report

    json_path = tmp_path / "matrix.json"
    report = load_report_from_text(_sample_report_json()).model_copy(
        update={
            "agent_vs_rag": AgentVsRag(
                n=15,
                chunker=ChunkerName.SECTION,
                mode=RetrievalMode.HYBRID,
                rag_recall_at_5=0.267,
                rag_recall_at_10=0.333,
                agent_recall_at_5=0.4,
                agent_recall_at_10=0.467,
            )
        }
    )
    json_path.write_text(report.model_dump_json(), encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(
        f"{BEGIN_MARK}\nOLD\n{END_MARK}\n{AGENT_BEGIN_MARK}\nOLD AGENT\n{AGENT_END_MARK}\n",
        encoding="utf-8",
    )
    update_readme(readme, load_report(json_path))
    text = readme.read_text(encoding="utf-8")
    assert "OLD AGENT" not in text
    assert "fake/heuristic planner" in text
    assert "0.267" in text
    assert "0.400" in text
    section = text.split(AGENT_BEGIN_MARK, 1)[1].split(AGENT_END_MARK, 1)[0]
    assert "Answer quality stays UNVERIFIED." in section
    assert "| — | UNVERIFIED" not in section


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
