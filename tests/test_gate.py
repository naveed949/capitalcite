from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.agent.tools import ToolRegistry
from capitalcite.agent.types import TOOL_NAMES, ToolResult
from capitalcite.cli import app
from capitalcite.config import get_settings
from capitalcite.gate import (
    BrokenPolicy,
    DenyReason,
    Gate,
    bench_decide,
    default_policy_path,
    load_policy,
    run_redteam,
)
from capitalcite.gate.policy import Policy
from capitalcite.mcp.names import MCP_ONLY_TOOLS
from capitalcite.trace import MemoryExporter, ObservationKind, configure_for_tests, reset_for_tests

runner = CliRunner()


def test_cli_help_lists_gate() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "gate" in result.stdout


def test_gate_help_lists_commands() -> None:
    result = runner.invoke(app, ["gate", "--help"], color=False)
    assert result.exit_code == 0
    assert "validate" in result.stdout
    assert "decide" in result.stdout
    assert "redteam" in result.stdout
    assert "bench" in result.stdout


def test_shipped_policy_lists_registered_tools() -> None:
    loaded = load_policy(None)
    assert isinstance(loaded, Policy)
    assert set(loaded.tools) == set(TOOL_NAMES) | MCP_ONLY_TOOLS
    assert MCP_ONLY_TOOLS.isdisjoint(TOOL_NAMES)
    assert loaded.default == "deny"
    assert default_policy_path().is_file()


def test_unknown_tool_is_denied() -> None:
    gate = Gate(load_policy(None), ToolRegistry({}))
    decision = gate.decide("shell", {"cmd": "ls"})
    assert decision.kind == "deny"
    assert decision.reason == DenyReason.unknown_tool
    assert decision.detail == "unknown tool"


def test_invalid_args_are_denied() -> None:
    gate = Gate(load_policy(None), ToolRegistry({}))
    missing = gate.decide("calculator", {})
    extra = gate.decide("calculator", {"expression": "1+1", "cmd": "id"})
    empty = gate.decide("search_filings", {"query": ""})
    assert missing.kind == "deny"
    assert missing.reason == DenyReason.invalid_args
    assert extra.reason == DenyReason.invalid_args
    assert empty.reason == DenyReason.invalid_args


async def test_deny_does_not_call_the_runner() -> None:
    calls: list[str] = []

    async def boom(args: Mapping[str, object]) -> ToolResult:
        calls.append("ran")
        return ToolResult(ok=True, tool="calculator", args=dict(args), detail="boom")

    gate = Gate(load_policy(None), ToolRegistry({"calculator": boom, "shell": boom}))
    unknown = await gate.execute("shell", {"cmd": "ls"})
    invalid = await gate.execute("calculator", {"expression": "1+1", "cmd": "id"})
    assert unknown.ok is False
    assert unknown.reason == "unknown_tool"
    assert invalid.ok is False
    assert invalid.reason == "invalid_args"
    assert calls == []


async def test_allow_calls_the_runner() -> None:
    async def add(args: Mapping[str, object]) -> ToolResult:
        return ToolResult(
            ok=True,
            tool="calculator",
            args=dict(args),
            detail="2",
            value="2",
        )

    gate = Gate(load_policy(None), ToolRegistry({"calculator": add}))
    result = await gate.execute("calculator", {"expression": "1+1"})
    assert result.ok is True
    assert result.value == "2"


def test_decide_records_allow_and_deny_spans() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    try:
        gate = Gate(load_policy(None), ToolRegistry({}))
        denied = gate.decide("shell", {"cmd": "ls"})
        allowed = gate.decide("calculator", {"expression": "1+1"})
        assert denied.kind == "deny"
        assert allowed.kind == "allow"
        records = [row for row in exporter.finished() if row.kind is ObservationKind.GATE]
        assert len(records) == 2
        deny, allow = records
        assert deny.attributes["capitalcite.tool"] == "shell"
        assert deny.attributes["capitalcite.gate.decision"] == "deny"
        assert deny.attributes["capitalcite.gate.reason"] == "unknown_tool"
        assert allow.attributes["capitalcite.tool"] == "calculator"
        assert allow.attributes["capitalcite.gate.decision"] == "allow"
        assert allow.attributes["capitalcite.gate.reason"] == "allow"
    finally:
        reset_for_tests()


async def test_execute_deny_records_gate_without_running_the_tool() -> None:
    exporter = MemoryExporter()
    configure_for_tests(exporter)
    calls: list[str] = []

    async def boom(args: Mapping[str, object]) -> ToolResult:
        calls.append("ran")
        return ToolResult(ok=True, tool="calculator", args=dict(args), detail="boom")

    try:
        gate = Gate(load_policy(None), ToolRegistry({"calculator": boom}))
        result = await gate.execute("shell", {"cmd": "ls"})
        assert result.ok is False
        assert calls == []
        kinds = [row.kind for row in exporter.finished()]
        assert ObservationKind.GATE in kinds
        assert ObservationKind.TOOL not in kinds
        (record,) = [row for row in exporter.finished() if row.kind is ObservationKind.GATE]
        assert record.attributes["capitalcite.gate.decision"] == "deny"
        assert record.attributes["capitalcite.gate.reason"] == "unknown_tool"
        assert record.attributes["capitalcite.tool"] == "shell"
    finally:
        reset_for_tests()


def test_missing_policy_file_denies(tmp_path: Path) -> None:
    loaded = load_policy(tmp_path / "missing.json")
    assert isinstance(loaded, BrokenPolicy)
    gate = Gate(loaded, ToolRegistry({}))
    decision = gate.decide("calculator", {"expression": "1+1"})
    assert decision.kind == "deny"
    assert decision.reason == DenyReason.policy_load_failed
    assert decision.detail == "policy load failed"


def test_invalid_json_policy_denies(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")
    loaded = load_policy(path)
    assert isinstance(loaded, BrokenPolicy)
    decision = Gate(loaded, ToolRegistry({})).decide("search_filings", {"query": "CET1"})
    assert decision.reason == DenyReason.policy_load_failed


def test_default_allow_does_not_load(tmp_path: Path) -> None:
    path = tmp_path / "open.json"
    path.write_text(
        json.dumps({"version": 1, "default": "allow", "tools": {}}),
        encoding="utf-8",
    )
    loaded = load_policy(path)
    assert isinstance(loaded, BrokenPolicy)
    decision = Gate(loaded, ToolRegistry({})).decide("calculator", {"expression": "1"})
    assert decision.reason == DenyReason.policy_load_failed


def test_budget_exceeded_after_max_calls(tmp_path: Path) -> None:
    path = tmp_path / "tight.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default": "deny",
                "tools": {
                    "calculator": {
                        "max_calls": 1,
                        "max_arg_bytes": 512,
                        "args": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["expression"],
                            "properties": {"expression": {"type": "string", "minLength": 1}},
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    gate = Gate(load_policy(path), ToolRegistry({}))
    first = gate.decide("calculator", {"expression": "1+1"})
    second = gate.decide("calculator", {"expression": "2+2"})
    assert first.kind == "allow"
    assert second.kind == "deny"
    assert second.reason == DenyReason.budget_exceeded
    assert second.detail == "budget exceeded"


def test_oversized_args_are_budget(tmp_path: Path) -> None:
    path = tmp_path / "tiny.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default": "deny",
                "tools": {
                    "calculator": {
                        "max_calls": 4,
                        "max_arg_bytes": 8,
                        "args": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["expression"],
                            "properties": {"expression": {"type": "string", "minLength": 1}},
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    decision = Gate(load_policy(path), ToolRegistry({})).decide(
        "calculator",
        {"expression": "1234567890"},
    )
    assert decision.reason == DenyReason.args_too_large
    assert decision.detail == "args too large"


def _cyclic_args() -> dict[object, object]:
    args: dict[object, object] = {}
    args["self"] = args
    return args


@pytest.mark.parametrize(
    ("label", "args"),
    [
        ("int-key", {1: "x"}),
        ("cyclic", _cyclic_args()),
        ("not-mapping", "notamapping"),
    ],
    ids=["int-key", "cyclic", "not-mapping"],
)
async def test_odd_args_deny_without_running_the_runner(
    label: str,
    args: object,
) -> None:
    _ = label
    calls: list[str] = []

    async def boom(payload: Mapping[str, object]) -> ToolResult:
        calls.append("ran")
        return ToolResult(ok=True, tool="calculator", args=dict(payload), detail="boom")

    gate = Gate(load_policy(None), ToolRegistry({"calculator": boom}))
    result = await gate.execute("calculator", args)  # type: ignore[arg-type]
    assert result.ok is False
    assert result.reason is not None
    assert calls == []


async def test_validate_args_raise_is_gate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(_schema: Mapping[str, object], _args: Mapping[str, object]) -> str | None:
        raise RuntimeError("schema exploded")

    monkeypatch.setattr("capitalcite.gate.gate.validate_args", explode)
    calls: list[str] = []

    async def boom(payload: Mapping[str, object]) -> ToolResult:
        calls.append("ran")
        return ToolResult(ok=True, tool="calculator", args=dict(payload), detail="boom")

    gate = Gate(load_policy(None), ToolRegistry({"calculator": boom}))
    result = await gate.execute("calculator", {"expression": "1+1"})
    assert result.ok is False
    assert result.detail == "gate error"
    assert result.reason == "gate_error"
    assert calls == []


async def test_redteam_suite_passes() -> None:
    report = await run_redteam()
    assert report.failed == 0
    assert report.passed == len(report.cases) == 8
    assert {case.id for case in report.cases} == {
        "deny-unknown-does-not-run",
        "injection-shell",
        "injection-extra-args",
        "policy-load-failure",
        "fake-planner-ignores-doc-injection",
        "metric-query-extra-sql",
        "metric-query-oversized",
        "metric-query-unknown-group-by",
    }


def test_cli_decide_unknown_tool() -> None:
    result = runner.invoke(
        app,
        ["gate", "decide", "--tool", "shell", "--args", '{"cmd":"ls"}'],
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["kind"] == "deny"
    assert body["reason"] == "unknown_tool"
    assert body["detail"] == "unknown tool"


def test_cli_decide_writes_a_gate_span(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "spans.jsonl"
    monkeypatch.setenv("CAPITALCITE_TRACE_FILE", str(path))
    get_settings.cache_clear()
    try:
        result = runner.invoke(
            app,
            ["gate", "decide", "--tool", "shell", "--args", '{"cmd":"ls"}'],
        )
        assert result.exit_code == 0
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(rows) == 1
        assert rows[0]["name"] == "gate"
        attrs = rows[0]["attributes"]
        assert attrs["capitalcite.tool"] == "shell"
        assert attrs["capitalcite.gate.decision"] == "deny"
        assert attrs["capitalcite.gate.reason"] == "unknown_tool"
    finally:
        get_settings.cache_clear()
        reset_for_tests()


def test_cli_decide_invalid_args() -> None:
    result = runner.invoke(
        app,
        ["gate", "decide", "--tool", "calculator", "--args", '{"expression":"1+1","cmd":"id"}'],
    )
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["kind"] == "deny"
    assert body["reason"] == "invalid_args"


def test_cli_validate_shipped_policy() -> None:
    result = runner.invoke(app, ["gate", "validate"])
    assert result.exit_code == 0
    assert "ok tools=" in result.stdout
    assert "calculator" in result.stdout


def test_cli_validate_broken_policy(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("[]", encoding="utf-8")
    result = runner.invoke(app, ["gate", "validate", "--policy", str(path)])
    assert result.exit_code == 1
    assert "policy_load_failed" in result.stderr


def test_cli_redteam() -> None:
    result = runner.invoke(app, ["gate", "redteam"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["failed"] == 0
    assert body["passed"] == 8


def test_cli_bench() -> None:
    result = runner.invoke(app, ["gate", "bench", "--n", "20"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["n"] == 20
    assert body["p95_ms"] >= 0
    report = bench_decide(20)
    assert report.n == 20
    assert report.p95_ms >= 0
