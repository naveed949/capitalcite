"""Wrap ToolRegistry.execute so each call writes one audit record.

Issue #9 replaces this wrapper with the fail-closed gate. The gate should
call AuditLog.record itself and must not also wrap with AuditingTools, or
allowed calls would be recorded twice. ToolRegistry.execute does not write.
"""

from __future__ import annotations

from collections.abc import Mapping

from capitalcite.agent.tools import ToolRegistry
from capitalcite.agent.types import ToolResult
from capitalcite.audit import AuditLog, Decision


class AuditingTools:
    """Same execute() contract as ToolRegistry. One record per call."""

    def __init__(self, inner: ToolRegistry, log: AuditLog, trace_id: str) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self._inner = inner
        self._log = log
        self._trace_id = trace_id.strip()

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        result = await self._inner.execute(name, args)
        decision: Decision = "denied" if result.detail == "unknown tool" else "allowed"
        self._log.record(
            trace_id=self._trace_id,
            tool=result.tool,
            args=result.args,
            decision=decision,
            output=result.detail,
        )
        return result
