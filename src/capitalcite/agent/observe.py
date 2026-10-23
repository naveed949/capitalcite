"""Side channel the graph uses to report plan and tool transitions."""

from __future__ import annotations

from collections.abc import Sequence

from capitalcite.agent.types import PlanStep, TraceStep


class Observe:
    """What one run reports. Implementations must not do I/O.

    ``plan`` receives the pending list the plan node is about to store.
    ``tool_call`` receives that pending step before ``tools.execute``.
    ``tool_finished`` receives the one ``TraceStep`` the act node appends,
    and ``ToolResult.reason``.
    """

    def plan(self, steps: Sequence[PlanStep]) -> None:
        return None

    def tool_call(self, step: PlanStep) -> None:
        return None

    def tool_finished(self, trace: TraceStep, reason: str | None) -> None:
        return None


class NullObserve(Observe):
    """Default for ``run_agent``. Methods return without doing work."""


def resolve_observe(observe: Observe | None) -> Observe:
    return observe if observe is not None else NullObserve()
