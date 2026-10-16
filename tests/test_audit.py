from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from capitalcite.agent.audited import AuditingTools
from capitalcite.agent.tools import ToolRegistry
from capitalcite.agent.types import ToolResult
from capitalcite.audit import GENESIS_HASH, AuditLog, AuditRecord, verify
from capitalcite.cli import app

runner = CliRunner()
_FIXED = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)


def test_record_chains_from_genesis(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", clock=lambda: _FIXED)
    first = log.record(
        trace_id="trace-a",
        tool="calculator",
        args={"expression": "1+1"},
        decision="allowed",
        output="2",
    )
    second = log.record(
        trace_id="trace-a",
        tool="shell",
        args={"cmd": "ls"},
        decision="denied",
        reason="unknown_tool",
        output="unknown tool",
    )
    assert first.reason is None
    assert second.reason == "unknown_tool"
    assert first.seq == 1
    assert first.prev_hash == GENESIS_HASH
    assert first.ts == "2026-10-16T12:00:00.000000Z"
    assert second.seq == 2
    assert second.prev_hash == first.hash
    assert first.hash != second.hash
    report = verify(tmp_path / "audit.jsonl")
    assert report.ok is True
    assert report.records == 2


def test_verify_detects_mutated_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock=lambda: _FIXED)
    log.record(
        trace_id="t",
        tool="calculator",
        args={"expression": "1"},
        decision="allowed",
        output="1",
    )
    log.record(
        trace_id="t",
        tool="calculator",
        args={"expression": "2"},
        decision="allowed",
        output="2",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = lines[0].replace("calculator", "shell", 1)
    path.write_text(tampered + "\n" + lines[1] + "\n", encoding="utf-8")
    report = verify(path)
    assert report.ok is False
    assert any("hash mismatch" in issue.reason for issue in report.issues)


def test_verify_detects_removed_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock=lambda: _FIXED)
    log.record(trace_id="t", tool="a", args={}, decision="allowed", output="1")
    log.record(trace_id="t", tool="b", args={}, decision="allowed", output="2")
    log.record(
        trace_id="t", tool="c", args={}, decision="denied", reason="unknown_tool", output="x"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    report = verify(path)
    assert report.ok is False
    assert any(
        "prev_hash mismatch" in issue.reason or "seq gap" in issue.reason for issue in report.issues
    )


def test_verify_detects_inserted_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, clock=lambda: _FIXED)
    log.record(trace_id="t", tool="a", args={}, decision="allowed", output="1")
    forged = AuditRecord(
        seq=2,
        ts="2026-10-16T12:00:00.000000Z",
        trace_id="t",
        tool="forged",
        args_digest="a" * 64,
        decision="allowed",
        output_digest="b" * 64,
        prev_hash="c" * 64,
        hash="d" * 64,
    )
    path.write_text(
        path.read_text(encoding="utf-8") + forged.model_dump_json() + "\n",
        encoding="utf-8",
    )
    report = verify(path)
    assert report.ok is False


def test_verify_missing_file(tmp_path: Path) -> None:
    report = verify(tmp_path / "missing.jsonl")
    assert report.ok is False
    assert report.records == 0
    assert "missing" in report.issues[0].reason


def test_verify_empty_file_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text("", encoding="utf-8")
    report = verify(path)
    assert report.ok is True
    assert report.records == 0


def test_same_args_share_a_digest(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", clock=lambda: _FIXED)
    a = log.record(
        trace_id="t",
        tool="calculator",
        args={"b": 2, "a": 1},
        decision="allowed",
        output="ok",
    )
    b = log.record(
        trace_id="t",
        tool="calculator",
        args={"a": 1, "b": 2},
        decision="allowed",
        output="ok",
    )
    assert a.args_digest == b.args_digest
    assert a.output_digest == b.output_digest


async def test_auditing_tools_writes_one_record_per_execute(tmp_path: Path) -> None:
    async def add(args: Mapping[str, object]) -> ToolResult:
        return ToolResult(ok=True, tool="calculator", args=dict(args), detail="2")

    path = tmp_path / "audit.jsonl"
    tools = AuditingTools(
        ToolRegistry({"calculator": add}),
        AuditLog(path, clock=lambda: _FIXED),
        "trace-live",
    )
    allowed = await tools.execute("calculator", {"expression": "1+1"})
    denied = await tools.execute("shell", {"cmd": "ls"})
    assert allowed.ok is True
    assert denied.ok is False
    assert denied.detail == "unknown tool"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    records = [AuditRecord.model_validate_json(line) for line in lines]
    assert [row.decision for row in records] == ["allowed", "denied"]
    assert [row.reason for row in records] == [None, "unknown_tool"]
    assert [row.tool for row in records] == ["calculator", "shell"]
    assert {row.trace_id for row in records} == {"trace-live"}
    report = verify(path)
    assert report.ok is True
    assert report.records == 2


def test_cli_verify_ok_and_fail(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path, clock=lambda: _FIXED).record(
        trace_id="t",
        tool="calculator",
        args={"expression": "1"},
        decision="allowed",
        output="1",
    )
    ok = runner.invoke(app, ["audit", "verify", "--path", str(path)])
    assert ok.exit_code == 0
    assert "audit ok records=1" in ok.stdout
    path.write_text(
        path.read_text(encoding="utf-8").replace("calculator", "nope", 1), encoding="utf-8"
    )
    bad = runner.invoke(app, ["audit", "verify", "--path", str(path)])
    assert bad.exit_code == 1
    assert "audit fail" in (bad.stdout + bad.stderr)


def test_cli_help_lists_audit() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "audit" in result.stdout
    nested = runner.invoke(app, ["audit", "verify", "--help"])
    assert nested.exit_code == 0
    assert "--path" in nested.stdout
