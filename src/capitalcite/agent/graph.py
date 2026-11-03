"""Plan-then-act graph. LangGraph owns one loop. A plain while-loop owns the other."""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from capitalcite.agent.models import AgentModel
from capitalcite.agent.observe import Observe, resolve_observe
from capitalcite.agent.tools import ToolExecutor
from capitalcite.agent.types import AgentResult, Budgets, PlanStep, TraceStep
from capitalcite.ask.service import ask
from capitalcite.ask.types import Answer, ModelDraft, Refused
from capitalcite.audit import AUDIT_UNAVAILABLE, AUDIT_UNAVAILABLE_DETAIL
from capitalcite.retrieval.types import Hit
from capitalcite.trace import ObservationKind, observation


class GraphState(TypedDict):
    question: str
    plan: list[PlanStep]
    traces: list[TraceStep]
    hits: list[Hit]
    cursor: int
    steps_used: int
    tokens_used: int
    max_steps: int
    max_tokens: int
    synthesize_now: bool
    answer: Answer | None
    stop_reason: str | None


@dataclass(frozen=True)
class _Runtime:
    model: AgentModel
    tools: ToolExecutor
    min_score: float
    observe: Observe


class _Synthesizer:
    def __init__(self, model: AgentModel) -> None:
        self._model = model

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        return await self._model.complete(question, hits)


_RUNTIME: ContextVar[_Runtime | None] = ContextVar("capitalcite_agent_runtime", default=None)
_COMPILED: Any = None
_COMPILE_ONCE = True


def set_compile_once(value: bool) -> None:
    global _COMPILE_ONCE, _COMPILED
    _COMPILE_ONCE = value
    if not value:
        _COMPILED = None


def compile_once_enabled() -> bool:
    return _COMPILE_ONCE


def reset_compiled_graph() -> None:
    global _COMPILED
    _COMPILED = None


def _runtime() -> _Runtime:
    current = _RUNTIME.get()
    if current is None:
        raise RuntimeError("agent runtime is not set")
    return current


def _over_budget(state: GraphState) -> str | None:
    if state["steps_used"] >= state["max_steps"]:
        return "step budget exceeded"
    if state["tokens_used"] >= state["max_tokens"]:
        return "token budget exceeded"
    return None


def _charge(state: GraphState, extra: int) -> int:
    return state["tokens_used"] + max(1, extra)


def initial_state(question: str, budgets: Budgets) -> GraphState:
    return {
        "question": question,
        "plan": [],
        "traces": [],
        "hits": [],
        "cursor": 0,
        "steps_used": 0,
        "tokens_used": 0,
        "max_steps": budgets.max_steps,
        "max_tokens": budgets.max_tokens,
        "synthesize_now": False,
        "answer": None,
        "stop_reason": None,
    }


def merge_state(state: GraphState, update: dict[str, object]) -> GraphState:
    merged = dict(state)
    merged.update(update)
    return cast(GraphState, merged)


async def plan_step(state: GraphState) -> dict[str, object]:
    with observation("graph.plan", ObservationKind.AGENT):
        runtime = _runtime()
        reason = _over_budget(state)
        if reason is not None:
            return {
                "stop_reason": reason,
                "answer": Refused(refusal_reason=reason),
            }
        decision = await runtime.model.plan(state["question"], state["traces"], state["hits"])
        tokens = _charge(state, 100 + len(state["question"]) // 4)
        steps = state["steps_used"] + 1
        if decision.kind == "synthesize":
            update: dict[str, object] = {
                "synthesize_now": True,
                "steps_used": steps,
                "tokens_used": tokens,
            }
            if decision.refusal_reason:
                update["answer"] = Refused(refusal_reason=decision.refusal_reason)
                update["stop_reason"] = decision.refusal_reason
            return update
        plan = [
            PlanStep(tool=call.name, args=dict(call.args), status="pending")
            for call in decision.calls
        ]
        runtime.observe.plan(plan)
        return {
            "plan": plan,
            "cursor": 0,
            "synthesize_now": False,
            "steps_used": steps,
            "tokens_used": tokens,
        }


async def act_step(state: GraphState) -> dict[str, object]:
    with observation("graph.act", ObservationKind.AGENT):
        runtime = _runtime()
        reason = _over_budget(state)
        if reason is not None:
            skipped = list(state["plan"])
            cursor = state["cursor"]
            for index in range(cursor, len(skipped)):
                step = skipped[index]
                skipped[index] = step.model_copy(update={"status": "skipped"})
            return {
                "plan": skipped,
                "stop_reason": reason,
                "answer": Refused(refusal_reason=reason),
            }
        step = state["plan"][state["cursor"]]
        runtime.observe.tool_call(step)
        result = await runtime.tools.execute(step.tool, step.args)
        traces = list(state["traces"])
        trace = TraceStep(
            tool=result.tool,
            args=result.args,
            ok=result.ok,
            detail=result.detail,
        )
        runtime.observe.tool_finished(
            trace, None if result.reason is None else str(result.reason)
        )
        traces.append(trace)
        plan = list(state["plan"])
        plan[state["cursor"]] = step.model_copy(
            update={"status": "done" if result.ok else "error"}
        )
        hits = list(state["hits"])
        hits.extend(result.hits)
        update: dict[str, object] = {
            "plan": plan,
            "traces": traces,
            "hits": hits,
            "cursor": state["cursor"] + 1,
            "steps_used": state["steps_used"] + 1,
            "tokens_used": _charge(state, 8 + len(result.detail) // 4),
        }
        if result.reason == AUDIT_UNAVAILABLE:
            update["stop_reason"] = AUDIT_UNAVAILABLE_DETAIL
            update["answer"] = Refused(refusal_reason=AUDIT_UNAVAILABLE_DETAIL)
        return update


async def synthesize_step(state: GraphState) -> dict[str, object]:
    with observation("graph.synthesize", ObservationKind.AGENT):
        runtime = _runtime()
        reason = _over_budget(state)
        if reason is not None:
            return {
                "stop_reason": reason,
                "answer": Refused(refusal_reason=reason),
            }
        answer = await ask(
            list(state["hits"]),
            state["question"],
            _Synthesizer(runtime.model),
            min_score=runtime.min_score,
        )
        return {
            "answer": answer,
            "steps_used": state["steps_used"] + 1,
            "tokens_used": _charge(
                state, 100 + sum(len(hit.chunk.text) for hit in state["hits"]) // 4
            ),
        }


def stop_step(state: GraphState) -> dict[str, object]:
    _ = state
    with observation("graph.stop", ObservationKind.AGENT):
        return {}


def after_plan(state: GraphState) -> Literal["stop", "synthesize", "act"]:
    if state.get("answer") is not None or state.get("stop_reason"):
        return "stop"
    if state.get("synthesize_now"):
        return "synthesize"
    if not state.get("plan"):
        return "synthesize"
    return "act"


def after_act(state: GraphState) -> Literal["stop", "synthesize", "act"]:
    if state.get("answer") is not None or state.get("stop_reason"):
        return "stop"
    if state["cursor"] < len(state["plan"]):
        return "act"
    return "synthesize"


def _result(state: GraphState) -> AgentResult:
    answer = state["answer"]
    if answer is None:
        reason = state["stop_reason"] or "agent stopped without an answer"
        answer = Refused(refusal_reason=reason)
    return AgentResult(
        answer=answer,
        plan=tuple(state["plan"]),
        traces=tuple(state["traces"]),
        hits=tuple(state["hits"]),
        steps_used=state["steps_used"],
        tokens_used=state["tokens_used"],
        stop_reason=state["stop_reason"],
    )


def _compile() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("plan", plan_step)
    builder.add_node("act", act_step)
    builder.add_node("synthesize", synthesize_step)
    builder.add_node("stop", stop_step)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan",
        after_plan,
        {"stop": "stop", "synthesize": "synthesize", "act": "act"},
    )
    builder.add_conditional_edges(
        "act",
        after_act,
        {"stop": "stop", "synthesize": "synthesize", "act": "act"},
    )
    builder.add_edge("synthesize", END)
    builder.add_edge("stop", END)
    return builder.compile()


def _graph() -> Any:
    global _COMPILED
    if _COMPILE_ONCE and _COMPILED is not None:
        return _COMPILED
    compiled = _compile()
    if _COMPILE_ONCE:
        _COMPILED = compiled
    return compiled


async def run_agent(
    question: str,
    model: AgentModel,
    tools: ToolExecutor,
    budgets: Budgets,
    *,
    min_score: float = 0.0,
    observe: Observe | None = None,
) -> AgentResult:
    """Run planner → tools → synthesizer. The synthesizer is ask()/ground()/guard."""
    token = _RUNTIME.set(
        _Runtime(
            model=model,
            tools=tools,
            min_score=min_score,
            observe=resolve_observe(observe),
        )
    )
    try:
        with observation("agent", ObservationKind.AGENT):
            with observation("compile", ObservationKind.AGENT):
                graph = _graph()
            raw = await graph.ainvoke(
                initial_state(question, budgets),
                {"recursion_limit": budgets.max_steps + 6},
            )
        return _result(cast(GraphState, raw))
    finally:
        _RUNTIME.reset(token)


async def run_agent_loop(
    question: str,
    model: AgentModel,
    tools: ToolExecutor,
    budgets: Budgets,
    *,
    min_score: float = 0.0,
    observe: Observe | None = None,
) -> AgentResult:
    """Same steps as run_agent, without a compiled StateGraph."""
    token = _RUNTIME.set(
        _Runtime(
            model=model,
            tools=tools,
            min_score=min_score,
            observe=resolve_observe(observe),
        )
    )
    try:
        with observation("agent", ObservationKind.AGENT):
            state = initial_state(question, budgets)
            state = merge_state(state, await plan_step(state))
            route = after_plan(state)
            while route == "act":
                state = merge_state(state, await act_step(state))
                route = after_act(state)
            if route == "synthesize":
                state = merge_state(state, await synthesize_step(state))
        return _result(state)
    finally:
        _RUNTIME.reset(token)
