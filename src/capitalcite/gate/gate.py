"""Decide allow or deny, then maybe run the tool. decide() does no tool I/O."""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from capitalcite.agent.tools import ToolExecutor
from capitalcite.agent.types import ToolResult
from capitalcite.gate.policy import BrokenPolicy, Policy
from capitalcite.gate.schema import validate_args
from capitalcite.trace import ObservationKind, gate_attrs, observation


class DenyReason(StrEnum):
    unknown_tool = "unknown_tool"
    invalid_args = "invalid_args"
    budget_exceeded = "budget_exceeded"
    args_too_large = "args_too_large"
    policy_load_failed = "policy_load_failed"
    gate_error = "gate_error"


_DETAIL = {
    DenyReason.unknown_tool: "unknown tool",
    DenyReason.invalid_args: "invalid args",
    DenyReason.budget_exceeded: "budget exceeded",
    DenyReason.args_too_large: "args too large",
    DenyReason.policy_load_failed: "policy load failed",
    DenyReason.gate_error: "gate error",
}


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["allow", "deny"]
    tool: str
    args: dict[str, object]
    reason: DenyReason | None = None
    detail: str

    @model_validator(mode="after")
    def legal_shape(self) -> Decision:
        if self.kind == "allow":
            if self.reason is not None:
                raise ValueError("allow must not include a deny reason")
            return self
        if self.reason is None:
            raise ValueError("deny needs a reason")
        return self


class Gate:
    """One request. Counters start at zero. A reused instance spends the same budget."""

    def __init__(self, policy: Policy | BrokenPolicy, inner: ToolExecutor) -> None:
        self._policy = policy
        self._inner = inner
        self._calls: dict[str, int] = {}

    def decide(self, name: str, args: Mapping[str, object]) -> Decision:
        with observation(
            "gate",
            ObservationKind.GATE,
            **gate_attrs(tool=name, decision="deny", reason=DenyReason.gate_error.value),
        ) as span:
            decision = self._evaluate(name, args)
            span.set(
                **gate_attrs(
                    tool=decision.tool,
                    decision=decision.kind,
                    reason=decision.reason.value if decision.reason is not None else "allow",
                )
            )
            return decision

    def _evaluate(self, name: str, args: Mapping[str, object]) -> Decision:
        recorded = _record_args(args)
        if isinstance(self._policy, BrokenPolicy):
            return _deny(name, recorded, DenyReason.policy_load_failed)
        rule = self._policy.tools.get(name)
        if rule is None:
            return _deny(name, recorded, DenyReason.unknown_tool)
        if not isinstance(args, Mapping):
            return _deny(name, recorded, DenyReason.invalid_args)
        try:
            copied = dict(args)
        except Exception:
            return _deny(name, recorded, DenyReason.invalid_args)
        self._calls[name] = self._calls.get(name, 0) + 1
        if self._calls[name] > rule.max_calls:
            return _deny(name, recorded, DenyReason.budget_exceeded)
        try:
            encoded = json.dumps(copied, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return _deny(name, recorded, DenyReason.invalid_args)
        if len(encoded.encode("utf-8")) > rule.max_arg_bytes:
            return _deny(name, recorded, DenyReason.args_too_large)
        if validate_args(rule.args, copied) is not None:
            return _deny(name, recorded, DenyReason.invalid_args)
        return Decision(kind="allow", tool=name, args=recorded, detail="allow")

    async def run(self, decision: Decision, args: Mapping[str, object] | None = None) -> ToolResult:
        """Apply a prior decide() result. Does not spend budget again."""
        if decision.kind == "deny":
            return ToolResult(
                ok=False,
                tool=decision.tool,
                args=decision.args,
                detail=decision.detail,
                reason=decision.reason,
            )
        payload = args if args is not None else decision.args
        return await self._inner.execute(decision.tool, payload)

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        try:
            decision = self.decide(name, args)
        except Exception:
            return ToolResult(
                ok=False,
                tool=name,
                args=_record_args(args),
                detail=_DETAIL[DenyReason.gate_error],
                reason=DenyReason.gate_error,
            )
        return await self.run(decision, args)


def _record_args(args: object) -> dict[str, object]:
    """Snapshot args so a deny Decision and ToolResult cannot raise."""
    try:
        if not isinstance(args, Mapping):
            return {"repr": repr(args)}
        recorded: dict[str, object] = {}
        for key, value in args.items():
            recorded[str(key)] = _safe_value(value)
        return recorded
    except Exception:
        try:
            return {"repr": repr(args)}
        except Exception:
            return {"repr": "<unrepresentable>"}


def _safe_value(value: object) -> object:
    try:
        json.dumps(value, default=str)
    except (TypeError, ValueError):
        return repr(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    return value


def _deny(name: str, args: object, reason: DenyReason) -> Decision:
    return Decision(
        kind="deny",
        tool=name,
        args=_record_args(args),
        reason=reason,
        detail=_DETAIL.get(reason, _DETAIL[DenyReason.gate_error]),
    )
