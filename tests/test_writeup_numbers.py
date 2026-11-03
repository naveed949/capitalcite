from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_writeup_numbers.py"


def run_check(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tree)],
        check=False,
        capture_output=True,
        text=True,
    )


def _arm(items: int, miss_count: int, late_ranks: list[int]) -> dict[str, object]:
    hits = items - miss_count
    misses = [f"m-{i}" for i in range(miss_count)]
    scores: list[dict[str, object]] = []
    for _ in range(hits):
        scores.append({"item_id": "hit", "gold_spans": 1, "covered_at": [1], "first_rank": 1})
    for rank in late_ranks:
        scores.append(
            {"item_id": "late", "gold_spans": 1, "covered_at": [rank], "first_rank": rank}
        )
    for _ in range(miss_count - len(late_ranks)):
        scores.append({"item_id": "miss", "gold_spans": 1, "covered_at": [], "first_rank": None})
    return {
        "metrics": {"items": items, "misses_at_5": misses},
        "item_scores": scores,
    }


def _write_tree(
    tree: Path,
    *,
    readme: str,
    writeup: str,
    results: dict[str, object] | None = None,
    result_name: str = "abcabcabcabc.json",
    git_sha: str = "abcabcabcabcdeadbeef",
) -> None:
    (tree / "docs").mkdir()
    (tree / "evals" / "results").mkdir(parents=True, exist_ok=True)
    (tree / "README.md").write_text(readme, encoding="utf-8")
    (tree / "docs" / "writeup.md").write_text(writeup, encoding="utf-8")
    if results is not None:
        payload = {"git_sha": git_sha, "small": False, **results}
        (tree / "evals" / "results" / result_name).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )


def test_allowed_tokens_exit_zero(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Recall@5 is 0.169 on n=77. Gate 5/5. Latency 14 ms.\n",
        writeup="section/hybrid Recall@5 is 0.169 on 77 items.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "writeup-numbers ok files=1 cited=" in proc.stdout


def test_missing_writeup_exits_one(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("0.169\n", encoding="utf-8")
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "docs/writeup.md:0:0: missing file" in proc.stdout


def test_writeup_rejects_json_only_number(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169. See [{name}](evals/results/{name}).\n",
        writeup="- Indexed 12431 points.\n",
        results={"point_count": 12431, "arms": []},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "12431" in proc.stdout
    assert "docs/writeup.md" in proc.stdout


def test_writeup_bench_rate_from_linked_json(tmp_path: Path) -> None:
    name = "abcabcabcabc-bench.json"
    link = f"[src](evals/results/{name})"
    queries = []
    for arm in ("rag", "graph", "loop"):
        queries.append(
            {
                "item_id": "cc-h-001",
                "arm": arm,
                "recall_at_5": True,
                "accurate": True,
                "phases": {"total_ms": 10.0, "compile_ms": 2.0},
            }
        )
        queries.append(
            {
                "item_id": "cc-h-002",
                "arm": arm,
                "recall_at_5": False,
                "accurate": False,
                "phases": {"total_ms": 12.0, "compile_ms": 2.0},
            }
        )
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.500 on n=2. Source {link}.\n",
        writeup=f"graph Recall@5 is 1/2 {link}.\n",
        results={"suite": "bench", "n_items": 2, "queries": queries},
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def _bench_queries() -> list[dict[str, object]]:
    queries: list[dict[str, object]] = []
    for arm in ("rag", "graph", "loop"):
        queries.append(
            {
                "item_id": "cc-h-001",
                "arm": arm,
                "recall_at_5": True,
                "accurate": True,
                "cost_usd": 0.0,
                "phases": {"total_ms": 10.0, "compile_ms": 2.0},
            }
        )
        queries.append(
            {
                "item_id": "cc-h-002",
                "arm": arm,
                "recall_at_5": False,
                "accurate": False,
                "cost_usd": 0.0,
                "phases": {"total_ms": 12.0, "compile_ms": 2.0},
            }
        )
    return queries


def test_readme_bench_block_matches_linked_json(tmp_path: Path) -> None:
    name = "abcabcabcabc-bench.json"
    link = f"[src](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=(
            f"See {link}.\n<!-- BEGIN BENCH EVAL -->\n"
            "n=2 Recall@5 is 0.500\n"
            "<!-- END BENCH EVAL -->\n"
        ),
        writeup=f"graph Recall@5 is 1/2 {link}.\n",
        results={"suite": "bench", "n_items": 2, "queries": _bench_queries()},
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_readme_bench_block_rejects_invented_rate(tmp_path: Path) -> None:
    name = "abcabcabcabc-bench.json"
    link = f"[src](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=(
            f"See {link}.\n<!-- BEGIN BENCH EVAL -->\nRecall@5 is 0.999\n<!-- END BENCH EVAL -->\n"
        ),
        writeup=f"graph Recall@5 is 1/2 {link}.\n",
        results={"suite": "bench", "n_items": 2, "queries": _bench_queries()},
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "0.999" in proc.stdout
    assert "README bench block" in proc.stdout
    assert "README.md:3:" in proc.stdout


def test_writeup_ratio_from_linked_json(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    link = f"[src](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169 on n=77. Source {link}.\n",
        writeup=f"Section hybrid is 13/77 {link}.\n",
        results={"arms": [_arm(77, 64, [6, 7])]},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_writeup_overhead_ms_from_linked_json(tmp_path: Path) -> None:
    name = "abcabcabcabc-otel-overhead.json"
    link = f"[overhead](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=f"OTLP adds 0.4 ms at p50. Source {link}.\n",
        writeup=f"OTLP adds 0.4 ms at p50 {link}.\n",
        results={
            "off": {"samples_ms": [2.0] * 20},
            "on": {"samples_ms": [2.4] * 20},
        },
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_readme_overhead_block_accepts_round_p50_diffs(tmp_path: Path) -> None:
    name = "abcabcabcabc-otel-overhead.json"
    link = f"[overhead](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=(
            f"Source {link}.\n"
            "<!-- BEGIN OTEL OVERHEAD -->\n"
            "Per-round p50 differences were 0.16 ms and 0.54 ms.\n"
            "<!-- END OTEL OVERHEAD -->\n"
        ),
        writeup=f"Per-round p50 differences were 0.16 ms and 0.54 ms {link}.\n",
        results={
            "off": {"samples_ms": [2.0] * 20 + [2.2] * 20},
            "on": {"samples_ms": [2.16] * 20 + [2.74] * 20},
            "round_p50_ms": {"off": [2.0, 2.2], "on": [2.16, 2.74]},
        },
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_readme_overhead_block_rejects_unprojected_ms(tmp_path: Path) -> None:
    name = "abcabcabcabc-otel-overhead.json"
    link = f"[overhead](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=(
            f"Measured 0.4 ms in {link}.\n"
            "<!-- BEGIN OTEL OVERHEAD -->\n"
            "OTLP adds 9.9 ms.\n"
            "<!-- END OTEL OVERHEAD -->\n"
        ),
        writeup=f"OTLP adds 0.4 ms {link}.\n",
        results={
            "off": {"samples_ms": [2.0] * 20},
            "on": {"samples_ms": [2.4] * 20},
        },
        result_name=name,
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "9.9 ms" in proc.stdout
    assert "OTLP overhead block" in proc.stdout


def _write_result(tree: Path, name: str, git_sha: str, results: dict[str, object]) -> None:
    payload = {"git_sha": git_sha, "small": False, **results}
    (tree / "evals" / "results" / name).write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def _bench_p50_results(total_ms: float, compile_ms: float) -> dict[str, object]:
    queries: list[dict[str, object]] = []
    for arm in ("rag", "graph", "loop"):
        queries.append(
            {
                "item_id": "cc-h-001",
                "arm": arm,
                "recall_at_5": True,
                "accurate": True,
                "cost_usd": 0.0,
                "phases": {"total_ms": total_ms, "compile_ms": compile_ms},
            }
        )
    return {"suite": "bench", "n_items": 1, "queries": queries}


def _overhead_plus_bench_tree(
    tree: Path,
    *,
    readme_block_ms: str,
    adr_block_ms: str | None = None,
) -> None:
    sha = "abcabcabcabcdeadbeef"
    overhead_name = "abcabcabcabc-otel-overhead.json"
    bench_name = "abcabcabcabc-bench.json"
    oh = f"evals/results/{overhead_name}"
    bh = f"evals/results/{bench_name}"
    _write_tree(
        tree,
        readme=(
            f"OTLP on was 2.6 ms. Graph p50 is 9.7 ms and compile is 6.9 ms. "
            f"See [{oh}]({oh}) and [{bh}]({bh}).\n"
            "<!-- BEGIN OTEL OVERHEAD -->\n"
            f"On was {readme_block_ms}.\n"
            "<!-- END OTEL OVERHEAD -->\n"
        ),
        writeup=(
            f"OTLP on was 2.6 ms [{oh}]({oh}). "
            f"Graph p50 is 9.7 ms and compile is 6.9 ms [{bh}]({bh}).\n"
        ),
        results={
            "off": {"samples_ms": [2.2] * 20},
            "on": {"samples_ms": [2.6] * 20},
        },
        result_name=overhead_name,
        git_sha=sha,
    )
    _write_result(tree, bench_name, sha, _bench_p50_results(9.7, 6.9))
    if adr_block_ms is not None:
        dest = tree / "docs" / "decisions"
        dest.mkdir()
        (dest / "0028-otel-tracing.md").write_text(
            (f"<!-- BEGIN OTEL OVERHEAD -->\nOn was {adr_block_ms}.\n<!-- END OTEL OVERHEAD -->\n"),
            encoding="utf-8",
        )


def test_readme_overhead_block_rejects_bench_p50_9_7(tmp_path: Path) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="9.7 ms")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "9.7 ms" in proc.stdout
    assert "OTLP overhead block" in proc.stdout


def test_readme_overhead_block_rejects_bench_p50_6_9(tmp_path: Path) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="6.9 ms")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "6.9 ms" in proc.stdout
    assert "OTLP overhead block" in proc.stdout


def test_adr_overhead_block_rejects_bench_p50_9_7(tmp_path: Path) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="2.6 ms", adr_block_ms="9.7 ms")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "9.7 ms" in proc.stdout
    assert "0028-otel-tracing.md" in proc.stdout


def test_adr_overhead_block_rejects_bench_p50_6_9(tmp_path: Path) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="2.6 ms", adr_block_ms="6.9 ms")
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "6.9 ms" in proc.stdout
    assert "0028-otel-tracing.md" in proc.stdout


def test_readme_overhead_block_keeps_true_on_p50_when_bench_differs(
    tmp_path: Path,
) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="2.6 ms")
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_readme_bench_block_rejects_overhead_on_p50(tmp_path: Path) -> None:
    _overhead_plus_bench_tree(tmp_path, readme_block_ms="2.6 ms")
    readme = tmp_path / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "<!-- BEGIN BENCH EVAL -->\nGraph p50 is 2.6 ms\n<!-- END BENCH EVAL -->\n",
        encoding="utf-8",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1, proc.stdout
    assert "2.6 ms" in proc.stdout
    assert "README bench block" in proc.stdout


def test_adr_overhead_block_rejects_unprojected_ms(tmp_path: Path) -> None:
    name = "abcabcabcabc-otel-overhead.json"
    link = f"[overhead](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=f"Measured 0.4 ms in {link}.\n",
        writeup=f"OTLP adds 0.4 ms {link}.\n",
        results={
            "off": {"samples_ms": [2.0] * 20},
            "on": {"samples_ms": [2.4] * 20},
        },
        result_name=name,
    )
    adr = tmp_path / "docs" / "decisions"
    adr.mkdir()
    (adr / "0028-otel-tracing.md").write_text(
        "<!-- BEGIN OTEL OVERHEAD -->\nOTLP adds 9.9 ms.\n<!-- END OTEL OVERHEAD -->\n",
        encoding="utf-8",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "9.9 ms" in proc.stdout
    assert "0028-otel-tracing.md" in proc.stdout


def test_writeup_ratio_without_link_fails(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169. See [src](evals/results/{name}).\n",
        writeup="Section hybrid is 13/77.\n",
        results={"arms": [_arm(77, 64, [])]},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "13/77" in proc.stdout


def test_twelve_not_allowed_by_one_twelve(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Golden set: 112 questions.\n",
        writeup="We indexed 12 filings.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "'12'" in proc.stdout


def test_ratio_is_atomic(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="n=77 and one item is 0.013. Hits were 13 in a probe.\n",
        writeup="The cell is 13/77.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "13/77" in proc.stdout


def test_structural_nonclaims_do_not_fail(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Recall@5 is 0.169. See ADR 0016.\n",
        writeup=(
            "Issue #17. ADR 0017. Date 2026-10-17. Year-end 2024. "
            "Run 7388ac3eea43. Line L199. Path evals/results/run.json. 0.169.\n"
        ),
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_unverified_cost_zero_not_in_readme_set(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Cost / query is [0](evals/results/run-unverified.json). Recall 0.169.\n",
        writeup="The OpenAI cost is 0 per query.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "cost line" in proc.stdout


def test_hyphenated_point_does_not_publish_twelve(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Timed on the 12-point fixture. Recall 0.169.\n",
        writeup="We used 12 shards.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "'12'" in proc.stdout


def test_diagnose_markdown_is_not_harvested(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    (tmp_path / "evals" / "results").mkdir(parents=True)
    (tmp_path / "evals" / "results" / "abcabcabcabc-bm25-diagnose.md").write_text(
        "gold_chunk_bm25_rank=113\n",
        encoding="utf-8",
    )
    _write_tree(
        tmp_path,
        readme=f"Recall 0.169. [src](evals/results/{name}).\n",
        writeup="Gold sat at rank 113 ([note](evals/results/abcabcabcabc-bm25-diagnose.md)).\n",
        results={"arms": [_arm(77, 64, [])]},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "113" in proc.stdout


def test_missing_linked_json_fails(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Recall 0.169. [src](evals/results/abcabcabcabc.json).\n",
        writeup="Recall 0.169 [src](evals/results/abcabcabcabc.json).\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "missing" in proc.stdout


def test_ci_workflow_runs_the_check() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/check_writeup_numbers.py" in text
    nightly = (ROOT / ".github" / "workflows" / "eval-nightly.yml").read_text(encoding="utf-8")
    assert "check_writeup_numbers.py" not in nightly


def test_repo_writeup_numbers_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout
    assert "writeup-numbers ok files=1 cited=" in proc.stdout
