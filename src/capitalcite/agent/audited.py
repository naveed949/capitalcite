from __future__ import annotations

from collections.abc import Mapping

from capitalcite.agent.tools import ToolExecutor
from capitalcite.agent.types import ToolResult
from capitalcite.audit import AuditLog, Decision


class AuditingTools:
    def __init__(self, inner: ToolExecutor, log: AuditLog, trace_id: str) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self._inner = inner
        self._log = log
        self._trace_id = trace_id.strip()

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        result = await self._inner.execute(name, args)
        reason = result.reason
        decision: Decision
        if reason:
            decision = "denied"
        elif result.detail == "unknown tool":
            decision = "denied"
            reason = "unknown_tool"
        else:
            decision = "allowed"
            reason = None
        self._log.record(
            trace_id=self._trace_id,
            tool=result.tool,
            args=result.args,
            decision=decision,
            output=result.detail,
            reason=reason,
        )
        return result
