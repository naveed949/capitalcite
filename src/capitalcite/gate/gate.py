"""Decide allow or deny, then maybe run the tool. decide() does no I/O."""

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


class DenyReason(StrEnum):
    unknown_tool = "unknown_tool"
    invalid_args = "invalid_args"
    budget_exceeded = "budget_exceeded"
    policy_load_failed = "policy_load_failed"


_DETAIL = {
    DenyReason.unknown_tool: "unknown tool",
    DenyReason.invalid_args: "invalid args",
    DenyReason.budget_exceeded: "budget exceeded",
    DenyReason.policy_load_failed: "policy load failed",
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
        copied = dict(args)
        if isinstance(self._policy, BrokenPolicy):
            return _deny(name, copied, DenyReason.policy_load_failed)
        rule = self._policy.tools.get(name)
        if rule is None:
            return _deny(name, copied, DenyReason.unknown_tool)
        self._calls[name] = self._calls.get(name, 0) + 1
        if self._calls[name] > rule.max_calls:
            return _deny(name, copied, DenyReason.budget_exceeded)
        try:
            encoded = json.dumps(copied, sort_keys=True, default=str)
        except TypeError:
            return _deny(name, copied, DenyReason.invalid_args)
        if len(encoded.encode("utf-8")) > rule.max_arg_bytes:
            return _deny(name, copied, DenyReason.budget_exceeded)
        if validate_args(rule.args, copied) is not None:
            return _deny(name, copied, DenyReason.invalid_args)
        return Decision(kind="allow", tool=name, args=copied, detail="allow")

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        decision = self.decide(name, args)
        if decision.kind == "deny":
            return ToolResult(
                ok=False,
                tool=name,
                args=dict(args),
                detail=decision.detail,
            )
        return await self._inner.execute(name, args)


def _deny(name: str, args: dict[str, object], reason: DenyReason) -> Decision:
    return Decision(
        kind="deny",
        tool=name,
        args=args,
        reason=reason,
        detail=_DETAIL[reason],
    )
