from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.cli import app
from capitalcite.eval.baseline import (
    Baseline,
    CompareReport,
    Thresholds,
    TrackedRate,
    compare,
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
    quote_drop: int = 1,
    fixture_drop: int = 1,
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


def test_compare_allows_one_quote_sanity_miss() -> None:
    committed = _snapshot(quote_hits=36)
    current = _snapshot(quote_hits=35)
    report = compare(current, committed)
    assert report.ok


def test_compare_fails_when_quote_sanity_drops_two_items() -> None:
    committed = _snapshot(quote_hits=36)
    current = _snapshot(quote_hits=34)
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
    assert snap.thresholds.quote_sanity_max_drop_items == 1
    assert snap.thresholds.fixture_max_drop_items == 1


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
