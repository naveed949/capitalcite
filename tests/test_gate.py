from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from typer.testing import CliRunner

from capitalcite.agent.tools import ToolRegistry
from capitalcite.agent.types import TOOL_NAMES, ToolResult
from capitalcite.cli import app
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
    assert set(loaded.tools) == set(TOOL_NAMES)
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
    assert invalid.ok is False
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
    assert decision.reason == DenyReason.budget_exceeded


async def test_redteam_suite_passes() -> None:
    report = await run_redteam()
    assert report.failed == 0
    assert report.passed == 5
    assert {case.id for case in report.cases} == {
        "deny-unknown-does-not-run",
        "injection-shell",
        "injection-extra-args",
        "policy-load-failure",
        "fake-planner-ignores-doc-injection",
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
    assert body["passed"] == 5


def test_cli_bench() -> None:
    result = runner.invoke(app, ["gate", "bench", "--n", "20"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["n"] == 20
    assert body["p95_ms"] >= 0
    report = bench_decide(20)
    assert report.n == 20
    assert report.p95_ms >= 0
