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
    resume: str,
    results: dict[str, object] | None = None,
    result_name: str = "abcabcabcabc.json",
    git_sha: str = "abcabcabcabcdeadbeef",
) -> None:
    (tree / "docs").mkdir()
    (tree / "evals" / "results").mkdir(parents=True)
    (tree / "README.md").write_text(readme, encoding="utf-8")
    (tree / "docs" / "writeup.md").write_text(writeup, encoding="utf-8")
    (tree / "docs" / "resume-bullets.md").write_text(resume, encoding="utf-8")
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
        resume="- Hit 0.169 Recall@5 ([run](evals/results/abcabcabcabc.json)).\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "writeup-numbers ok files=2 cited=" in proc.stdout


def test_missing_writeup_exits_one(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("0.169\n", encoding="utf-8")
    (tmp_path / "docs" / "resume-bullets.md").write_text("0.169\n", encoding="utf-8")
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "docs/writeup.md:0:0: missing file" in proc.stdout


def test_resume_rejects_json_only_number(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169. See [{name}](evals/results/{name}).\n",
        writeup=f"Recall@5 is 0.169 ([src](evals/results/{name})).\n",
        resume="- Indexed 12431 points.\n",
        results={"point_count": 12431, "arms": []},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "12431" in proc.stdout
    assert "docs/resume-bullets.md" in proc.stdout


def test_writeup_ratio_from_linked_json(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    link = f"[src](evals/results/{name})"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169 on n=77. Source {link}.\n",
        writeup=f"Section hybrid is 13/77 {link}.\n",
        resume="- Recall@5 0.169.\n",
        results={"arms": [_arm(77, 64, [6, 7])]},
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_writeup_ratio_without_link_fails(tmp_path: Path) -> None:
    name = "abcabcabcabc.json"
    _write_tree(
        tmp_path,
        readme=f"Recall@5 is 0.169. See [src](evals/results/{name}).\n",
        writeup="Section hybrid is 13/77.\n",
        resume="- Recall@5 0.169.\n",
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
        resume="- 112 questions.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "'12'" in proc.stdout


def test_ratio_is_atomic(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="n=77 and one item is 0.013. Hits were 13 in a probe.\n",
        writeup="The cell is 13/77.\n",
        resume="- n=77.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "13/77" in proc.stdout


def test_structural_nonclaims_do_not_fail(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Recall@5 is 0.169. See ADR 0016.\n",
        writeup=(
            "Issue #17. ADR 0017. Date 2026-10-17. "
            "Run 7388ac3eea43. Line L199. Path evals/results/run.json. 0.169.\n"
        ),
        resume="- 0.169 at L79.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 0, proc.stdout


def test_unverified_cost_zero_not_in_readme_set(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Cost / query is [0](evals/results/run-unverified.json). Recall 0.169.\n",
        writeup="The OpenAI cost is 0 per query.\n",
        resume="- 0.169.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "cost line" in proc.stdout


def test_hyphenated_point_does_not_publish_twelve(tmp_path: Path) -> None:
    _write_tree(
        tmp_path,
        readme="Timed on the 12-point fixture. Recall 0.169.\n",
        writeup="We used 12 shards.\n",
        resume="- 0.169.\n",
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
        resume="- 0.169.\n",
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
        resume="- 0.169.\n",
    )
    proc = run_check(tmp_path)
    assert proc.returncode == 1
    assert "missing" in proc.stdout


def test_ci_workflow_runs_the_check() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/check_writeup_numbers.py" in text
    nightly = (ROOT / ".github" / "workflows" / "eval-nightly.yml").read_text(
        encoding="utf-8"
    )
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
    assert "writeup-numbers ok files=2 cited=" in proc.stdout
