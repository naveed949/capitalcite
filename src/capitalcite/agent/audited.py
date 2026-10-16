from __future__ import annotations

import uuid
from collections.abc import Mapping

from capitalcite.agent.types import ToolResult
from capitalcite.audit import AUDIT_UNAVAILABLE, AUDIT_UNAVAILABLE_DETAIL, AuditLog, Decision
from capitalcite.gate.gate import Gate
from capitalcite.trace import current_trace_id


def request_trace_id() -> str:
    """Langfuse/root span id when tracing is on. UUID only when it is off."""
    return current_trace_id() or uuid.uuid4().hex


class AuditingTools:
    def __init__(self, inner: Gate, log: AuditLog, trace_id: str) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self._inner = inner
        self._log = log
        self._trace_id = trace_id.strip()

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        try:
            decision = self._inner.decide(name, args)
        except Exception:
            return ToolResult(
                ok=False,
                tool=name,
                args=dict(args),
                detail="gate error",
                reason="gate_error",
            )
        audit_decision: Decision
        reason: str | None
        if decision.kind == "allow":
            audit_decision = "allowed"
            reason = None
        else:
            audit_decision = "denied"
            reason = decision.reason.value if decision.reason is not None else "gate_error"
        try:
            self._log.record(
                trace_id=self._trace_id,
                tool=decision.tool,
                args=decision.args,
                decision=audit_decision,
                output=decision.detail,
                reason=reason,
            )
        except Exception:
            return ToolResult(
                ok=False,
                tool=decision.tool,
                args=decision.args,
                detail=AUDIT_UNAVAILABLE_DETAIL,
                reason=AUDIT_UNAVAILABLE,
            )
        return await self._inner.run(decision, args)
