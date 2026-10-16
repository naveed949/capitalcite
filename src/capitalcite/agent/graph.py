"""Plan-then-act graph. LangGraph owns the loop. ground() and guard own the answer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from capitalcite.agent.models import AgentModel
from capitalcite.agent.tools import ToolExecutor
from capitalcite.agent.types import AgentResult, Budgets, PlanStep, TraceStep
from capitalcite.ask.service import ask
from capitalcite.ask.types import Answer, ModelDraft, Refused
from capitalcite.retrieval.types import Hit


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


class _Synthesizer:
    def __init__(self, model: AgentModel) -> None:
        self._model = model

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        return await self._model.complete(question, hits)


def _over_budget(state: GraphState) -> str | None:
    if state["steps_used"] >= state["max_steps"]:
        return "step budget exceeded"
    if state["tokens_used"] >= state["max_tokens"]:
        return "token budget exceeded"
    return None


def _charge(state: GraphState, extra: int) -> int:
    return state["tokens_used"] + max(1, extra)


async def run_agent(
    question: str,
    model: AgentModel,
    tools: ToolExecutor,
    budgets: Budgets,
    *,
    min_score: float = 0.0,
) -> AgentResult:
    """Run planner → tools → synthesizer. The synthesizer is ask()/ground()/guard."""
    graph = _compile(model, tools, min_score)
    raw = await graph.ainvoke(
        {
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
        },
        {"recursion_limit": budgets.max_steps + 6},
    )
    state = cast(GraphState, raw)
    answer = state["answer"]
    if answer is None:
        reason = state["stop_reason"] or "agent stopped without an answer"
        answer = Refused(refusal_reason=reason)
    return AgentResult(
        answer=answer,
        plan=tuple(state["plan"]),
        traces=tuple(state["traces"]),
        steps_used=state["steps_used"],
        tokens_used=state["tokens_used"],
        stop_reason=state["stop_reason"],
    )


def _compile(model: AgentModel, tools: ToolExecutor, min_score: float) -> Any:
    async def plan_node(state: GraphState) -> dict[str, object]:
        reason = _over_budget(state)
        if reason is not None:
            return {
                "stop_reason": reason,
                "answer": Refused(refusal_reason=reason),
            }
        decision = await model.plan(state["question"], state["traces"], state["hits"])
        tokens = _charge(state, 100 + len(state["question"]) // 4)
        steps = state["steps_used"] + 1
        if decision.kind == "synthesize":
            return {
                "synthesize_now": True,
                "steps_used": steps,
                "tokens_used": tokens,
            }
        plan = [
            PlanStep(tool=call.name, args=dict(call.args), status="pending")
            for call in decision.calls
        ]
        return {
            "plan": plan,
            "cursor": 0,
            "synthesize_now": False,
            "steps_used": steps,
            "tokens_used": tokens,
        }

    async def act_node(state: GraphState) -> dict[str, object]:
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
        result = await tools.execute(step.tool, step.args)
        traces = list(state["traces"])
        traces.append(
            TraceStep(
                tool=result.tool,
                args=result.args,
                ok=result.ok,
                detail=result.detail,
            )
        )
        plan = list(state["plan"])
        plan[state["cursor"]] = step.model_copy(update={"status": "done" if result.ok else "error"})
        hits = list(state["hits"])
        hits.extend(result.hits)
        return {
            "plan": plan,
            "traces": traces,
            "hits": hits,
            "cursor": state["cursor"] + 1,
            "steps_used": state["steps_used"] + 1,
            "tokens_used": _charge(state, 8 + len(result.detail) // 4),
        }

    async def synthesize_node(state: GraphState) -> dict[str, object]:
        reason = _over_budget(state)
        if reason is not None:
            return {
                "stop_reason": reason,
                "answer": Refused(refusal_reason=reason),
            }
        answer = await ask(
            list(state["hits"]),
            state["question"],
            _Synthesizer(model),
            min_score=min_score,
        )
        return {
            "answer": answer,
            "steps_used": state["steps_used"] + 1,
            "tokens_used": _charge(
                state, 100 + sum(len(hit.chunk.text) for hit in state["hits"]) // 4
            ),
        }

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

    builder = StateGraph(GraphState)
    builder.add_node("plan", plan_node)
    builder.add_node("act", act_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_node("stop", lambda state: {})
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
