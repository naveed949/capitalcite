from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.cli import app
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
    load_nightly,
    snapshot_nightly,
)
from capitalcite.eval.check import (
    DEFAULT_BASELINE,
    CheckReport,
    ContractResult,
    LiveStatus,
    load_baseline,
)

runner = CliRunner()


def _snapshot(
    *,
    quote_hits: int = 36,
    quote_items: int = 77,
    fixture_hits: int = 3,
    fixture_items: int = 3,
    n_items: int = 77,
    arms: int = 6,
    embedder: str = "fake:deterministic",
    quote_drop: int = 0,
    fixture_drop: int = 0,
) -> Baseline:
    return Baseline(
        quote_sanity=TrackedRate(hits=quote_hits, items=quote_items),
        fixture=TrackedRate(hits=fixture_hits, items=fixture_items),
        small_n_items=n_items,
        small_arms=arms,
        small_embedder_id=embedder,
        thresholds=Thresholds(
            quote_sanity_max_drop_items=quote_drop,
            fixture_max_drop_items=fixture_drop,
        ),
    )


def test_compare_passes_on_equal_snapshot() -> None:
    snap = _snapshot()
    report = compare(snap, snap)
    assert report.ok
    assert report.failures == ()


def test_compare_allows_one_quote_sanity_miss_when_budget_is_one() -> None:
    committed = _snapshot(quote_hits=36, quote_drop=1)
    current = _snapshot(quote_hits=35, quote_drop=1)
    report = compare(current, committed)
    assert report.ok


def test_compare_zero_budget_fails_on_one_quote_sanity_miss() -> None:
    committed = _snapshot(quote_hits=36)
    current = _snapshot(quote_hits=35)
    report = compare(current, committed)
    assert not report.ok
    assert report.failures[0].metric == "quote_sanity"
    assert "dropped 1" in report.failures[0].detail


def test_compare_fails_when_quote_sanity_drops_two_items() -> None:
    committed = _snapshot(quote_hits=36, quote_drop=1)
    current = _snapshot(quote_hits=34, quote_drop=1)
    report = compare(current, committed)
    assert not report.ok
    assert report.failures[0].metric == "quote_sanity"
    assert "dropped 2" in report.failures[0].detail


def test_compare_fails_when_item_count_changes() -> None:
    committed = _snapshot(quote_items=77)
    current = _snapshot(quote_hits=36, quote_items=76)
    report = compare(current, committed)
    assert not report.ok
    assert report.failures[0].metric == "quote_sanity"
    assert "items 76" in report.failures[0].detail


def test_compare_fails_when_embedder_is_not_fake() -> None:
    committed = _snapshot()
    current = _snapshot(embedder="fastembed:BAAI/bge-small-en-v1.5")
    report = compare(current, committed)
    assert not report.ok
    assert report.failures[0].metric == "small_embedder_id"


def test_compare_fixture_drop_uses_item_budget() -> None:
    committed = _snapshot(fixture_hits=3, fixture_drop=1)
    allowed = compare(_snapshot(fixture_hits=2, fixture_drop=1), committed)
    blocked = compare(_snapshot(fixture_hits=1, fixture_drop=1), committed)
    assert allowed.ok
    assert not blocked.ok
    assert blocked.failures[0].metric == "fixture"


def test_compare_zero_fixture_budget_fails_on_one_miss() -> None:
    committed = _snapshot(fixture_hits=3)
    report = compare(_snapshot(fixture_hits=2), committed)
    assert not report.ok
    assert report.failures[0].metric == "fixture"


def _nightly(
    *,
    hits: dict[tuple[str, str], int] | None = None,
    n_items: int = 77,
    embedder: str = "fastembed:BAAI/bge-small-en-v1.5",
    sparse: str = "fastembed:Qdrant/bm25:k1=1.2:b=0.75:avgdl=256.0",
    drop: int = 1,
) -> NightlyBaseline:
    rates = hits or {
        ("section", "dense"): 6,
        ("section", "bm25"): 14,
        ("section", "hybrid"): 13,
        ("fixed", "dense"): 13,
        ("fixed", "bm25"): 10,
        ("fixed", "hybrid"): 15,
    }
    return NightlyBaseline(
        n_items=n_items,
        embedder_id=embedder,
        sparse_id=sparse,
        arms=tuple(
            NightlyArm(
                chunker=chunker,
                mode=mode,
                recall_at_5=TrackedRate(hits=count, items=n_items),
            )
            for (chunker, mode), count in rates.items()
        ),
        thresholds=NightlyThresholds(max_drop_items=drop),
    )


def test_compare_nightly_allows_one_item_drop() -> None:
    committed = _nightly()
    current_hits = {
        ("section", "dense"): 6,
        ("section", "bm25"): 13,
        ("section", "hybrid"): 13,
        ("fixed", "dense"): 13,
        ("fixed", "bm25"): 10,
        ("fixed", "hybrid"): 15,
    }
    report = compare_nightly(_nightly(hits=current_hits), committed)
    assert report.ok


def test_compare_nightly_fails_on_two_item_drop() -> None:
    committed = _nightly()
    current_hits = {
        ("section", "dense"): 6,
        ("section", "bm25"): 12,
        ("section", "hybrid"): 13,
        ("fixed", "dense"): 13,
        ("fixed", "bm25"): 10,
        ("fixed", "hybrid"): 15,
    }
    report = compare_nightly(_nightly(hits=current_hits), committed)
    assert not report.ok
    assert report.failures[0].metric == "section/bm25/recall_at_5"
    assert "dropped 2" in report.failures[0].detail


def test_compare_nightly_fails_on_arm_identity_drift() -> None:
    committed = _nightly()
    current = _nightly(
        hits={
            ("section", "dense"): 6,
            ("section", "bm25"): 14,
            ("section", "hybrid"): 13,
        }
    )
    report = compare_nightly(current, committed)
    assert not report.ok
    assert report.failures[0].metric == "arms"


def test_committed_nightly_baseline_matches_published_matrix() -> None:
    from capitalcite.eval.report import load_report

    snap = load_nightly(DEFAULT_NIGHTLY)
    assert snap.n_items == 77
    assert snap.embedder_id == "fastembed:BAAI/bge-small-en-v1.5"
    assert snap.thresholds.max_drop_items == 1
    by_arm = {(arm.chunker, arm.mode): arm.recall_at_5.hits for arm in snap.arms}
    assert by_arm == {("section", "hybrid"): 13}
    published = load_report(Path("evals/results/8b63d12acd50.json"))
    gated = snapshot_nightly(published)
    gated = gated.model_copy(
        update={
            "arms": tuple(
                arm for arm in gated.arms if arm.chunker == "section" and arm.mode == "hybrid"
            )
        }
    )
    assert compare_nightly(gated, snap).ok


def test_snapshot_nightly_rejects_small() -> None:
    from capitalcite.eval.models import EvalReport

    report = EvalReport(
        git_sha="b" * 40,
        suite="golden",
        small=True,
        k=5,
        n_items=77,
        embedder_id="fake:deterministic",
        arms=(),
    )
    with pytest.raises(ValueError, match="twelve-filing"):
        snapshot_nightly(report)


def test_check_report_fails_on_contract_miss() -> None:
    report = CheckReport(
        snapshot=_snapshot(),
        redteam_passed=5,
        redteam_failed=0,
        contracts=(
            ContractResult(
                id="cc-ci-002",
                expected="answered",
                observed="refused",
                ok=False,
                detail="expected grounded citations",
            ),
        ),
        live=LiveStatus(kind="skipped"),
        compare=CompareReport(ok=True, failures=()),
    )
    assert report.ok is False


def test_check_report_fails_on_redteam() -> None:
    report = CheckReport(
        snapshot=_snapshot(),
        redteam_passed=4,
        redteam_failed=1,
        contracts=(),
        live=LiveStatus(kind="skipped"),
        compare=CompareReport(ok=True, failures=()),
    )
    assert report.ok is False


def test_check_report_fails_on_live_error() -> None:
    report = CheckReport(
        snapshot=_snapshot(),
        redteam_passed=5,
        redteam_failed=0,
        contracts=(),
        live=LiveStatus(kind="failed", detail="openai returned no grounded citations"),
        compare=CompareReport(ok=True, failures=()),
    )
    assert report.ok is False


def test_check_report_unverified_live_still_ok() -> None:
    report = CheckReport(
        snapshot=_snapshot(),
        redteam_passed=5,
        redteam_failed=0,
        contracts=(
            ContractResult(id="cc-ci-002", expected="answered", observed="answered", ok=True),
            ContractResult(id="cc-u-001", expected="refused", observed="refused", ok=True),
        ),
        live=LiveStatus(kind="unverified", detail="OPENAI_API_KEY is empty"),
        compare=CompareReport(ok=True, failures=()),
    )
    assert report.ok is True


def test_cli_help_lists_check() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.stdout


def test_check_help_lists_baseline_and_live() -> None:
    result = runner.invoke(app, ["check", "--help"])
    assert result.exit_code == 0
    assert "--baseline" in result.stdout
    assert "--live" in result.stdout
    assert "--write-baseline" in result.stdout
    assert "--small" not in result.stdout


def test_committed_baseline_is_fake_embedder_snapshot() -> None:
    snap = load_baseline(DEFAULT_BASELINE)
    assert snap.quote_sanity.items == 77
    assert snap.quote_sanity.hits == 36
    assert snap.fixture == TrackedRate(hits=3, items=3)
    assert snap.small_n_items == 77
    assert snap.small_arms == 6
    assert snap.small_embedder_id == "fake:deterministic"
    assert snap.thresholds.quote_sanity_max_drop_items == 0
    assert snap.thresholds.fixture_max_drop_items == 0


def test_check_passes_committed_baseline(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check", "--out", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert "ok=true" in result.output
    assert "redteam_failed=0" in result.output
    body = json.loads((tmp_path / "out" / "check.json").read_text(encoding="utf-8"))
    assert body["ok"] is True
    assert body["snapshot"]["small_embedder_id"] == "fake:deterministic"
    assert body["live"]["kind"] == "skipped"


def test_check_missing_baseline_exits_1(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    result = runner.invoke(app, ["check", "--baseline", str(missing)])
    assert result.exit_code == 1
    assert "missing baseline" in result.output


def test_check_live_without_key_is_unverified_then_inflated_baseline_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    baseline = tmp_path / "ci.json"
    written = runner.invoke(
        app,
        [
            "check",
            "--write-baseline",
            "--baseline",
            str(baseline),
            "--live",
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert written.exit_code == 0, written.output
    assert "unverified:" in written.output
    assert "OPENAI_API_KEY is empty" in written.output
    body = json.loads((tmp_path / "out" / "check.json").read_text(encoding="utf-8"))
    assert body["live"]["kind"] == "unverified"
    assert body["ok"] is True
    assert baseline.is_file()
    snapshot = json.loads(baseline.read_text(encoding="utf-8"))
    snapshot["quote_sanity"]["hits"] = int(snapshot["quote_sanity"]["hits"]) + 2
    baseline.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    failed = runner.invoke(
        app,
        ["check", "--baseline", str(baseline), "--out", str(tmp_path / "fail")],
    )
    assert failed.exit_code == 1, failed.output
    assert "quote_sanity" in failed.output
