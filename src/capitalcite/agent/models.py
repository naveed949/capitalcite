"""Planner + synthesizer adapters. Fake is deterministic. OpenAI uses json_object."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Protocol

import httpx
from pydantic import ValidationError

from capitalcite.agent.types import PlannerDecision, ToolCall, TraceStep
from capitalcite.ask.guard import tickers_named, years_named
from capitalcite.ask.models import OpenAIStructuredModel, StructuredModel, _usage_tokens
from capitalcite.ask.types import (
    AskModelName,
    DraftCite,
    ModelDraft,
    ModelNotConfigured,
    ModelUnavailable,
)
from capitalcite.retrieval.types import Hit
from capitalcite.trace import ObservationKind, generation_attrs, observation

_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class AgentModel(Protocol):
    """Plan the next tools, then draft a cited answer over accumulated hits."""

    async def plan(
        self,
        question: str,
        traces: Sequence[TraceStep],
        hits: Sequence[Hit],
    ) -> PlannerDecision: ...

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft: ...


class FakeAgentModel:
    """Searches each named ticker/year, then quotes one hit per entity. No network."""

    def __init__(
        self,
        *,
        plan_script: PlannerDecision | None = None,
        draft: ModelDraft | None = None,
        synthesizer: StructuredModel | None = None,
    ) -> None:
        self._plan_script = plan_script
        self._draft = draft
        self._synthesizer = synthesizer
        self.plan_calls = 0
        self.complete_calls = 0

    async def plan(
        self,
        question: str,
        traces: Sequence[TraceStep],
        hits: Sequence[Hit],
    ) -> PlannerDecision:
        self.plan_calls += 1
        with observation("plan", ObservationKind.GENERATE, **generation_attrs(model="fake")):
            return self._plan(question, traces, hits)

    def _plan(
        self,
        question: str,
        traces: Sequence[TraceStep],
        hits: Sequence[Hit],
    ) -> PlannerDecision:
        _ = hits
        if self._plan_script is not None:
            return self._plan_script
        searched = {
            (
                str(step.args.get("ticker") or ""),
                str(step.args.get("period") or ""),
            )
            for step in traces
            if step.tool == "search_filings"
        }
        tickers = sorted(tickers_named(question)) or [""]
        years = [str(year) for year in sorted(years_named(question))] or [""]
        calls: list[ToolCall] = []
        for ticker in tickers:
            for year in years:
                key = (ticker, year)
                if key in searched:
                    continue
                args: dict[str, object] = {"query": question}
                if ticker:
                    args["ticker"] = ticker
                if year:
                    args["period"] = year
                calls.append(ToolCall(name="search_filings", args=args))
        graph_call = _graph_call(question, traces)
        if graph_call is not None:
            calls.insert(0, graph_call)
        if calls:
            return PlannerDecision(kind="plan", calls=tuple(calls))
        if not traces:
            return PlannerDecision(
                kind="plan",
                calls=(ToolCall(name="search_filings", args={"query": question}),),
            )
        return PlannerDecision(kind="synthesize")

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        self.complete_calls += 1
        if self._synthesizer is not None:
            return await self._synthesizer.complete(question, hits)
        with observation("complete", ObservationKind.GENERATE, **generation_attrs(model="fake")):
            if self._draft is not None:
                return self._draft
            chosen = _hits_for_question(question, hits)
            if not chosen:
                return ModelDraft(kind="refused", refusal_reason="no passages were retrieved")
            citations = tuple(DraftCite(index=index, quote=hit.chunk.text) for index, hit in chosen)
            answer = " ".join(hit.chunk.text for _index, hit in chosen)
            return ModelDraft(kind="answered", answer=answer, citations=citations)


class OpenAIAgentModel:
    """One json_object call to plan, then OpenAIStructuredModel for the draft."""

    def __init__(
        self,
        inner: OpenAIStructuredModel,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._inner = inner
        self._api_key = api_key
        self._model = model
        self._transport = transport

    async def plan(
        self,
        question: str,
        traces: Sequence[TraceStep],
        hits: Sequence[Hit],
    ) -> PlannerDecision:
        messages = [
            {"role": "system", "content": _plan_system_prompt()},
            {"role": "user", "content": _plan_user_prompt(question, traces, hits)},
        ]
        failure: str | None = None
        for _ in range(2):
            if failure is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous JSON failed validation: "
                            f"{failure}. Return one JSON object that matches the schema."
                        ),
                    }
                )
            content: str | None = None
            try:
                content = await self._post(messages)
                return _decision_from_content(content)
            except ValueError as exc:
                failure = str(exc)
                if content is not None:
                    messages.append({"role": "assistant", "content": content})
        return PlannerDecision(kind="synthesize")

    async def complete(self, question: str, hits: Sequence[Hit]) -> ModelDraft:
        return await self._inner.complete(question, hits)

    async def _post(self, messages: list[dict[str, str]]) -> str:
        with observation(
            "plan",
            ObservationKind.GENERATE,
            **generation_attrs(model=self._model),
        ) as span:
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
                    response = await client.post(
                        _CHAT_URL,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={
                            "model": self._model,
                            "response_format": {"type": "json_object"},
                            "messages": messages,
                        },
                    )
            except httpx.HTTPError as exc:
                raise ModelUnavailable("openai chat completion request failed") from exc
            if response.status_code != 200:
                raise ModelUnavailable(f"openai chat completion HTTP {response.status_code}")
            try:
                payload = response.json()
            except json.JSONDecodeError as exc:
                raise ModelUnavailable("openai chat completion response was not JSON") from exc
            content = _message_content(payload)
            input_tokens, output_tokens = _usage_tokens(payload)
            span.set(
                **generation_attrs(
                    model=self._model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
            return content


def build_agent_model(
    name: AskModelName,
    *,
    api_key: str | None,
    openai_model: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AgentModel:
    if name == "fake":
        return FakeAgentModel()
    key = (api_key or "").strip()
    if not key:
        raise ModelNotConfigured("ASK_MODEL=openai requires OPENAI_API_KEY")
    inner = OpenAIStructuredModel(key, openai_model, transport=transport)
    return OpenAIAgentModel(inner, key, openai_model, transport=transport)


def _hits_for_question(question: str, hits: Sequence[Hit]) -> list[tuple[int, Hit]]:
    needed_tickers = tickers_named(question)
    needed_years = years_named(question)
    chosen: list[tuple[int, Hit]] = []
    seen_tickers: set[str] = set()
    seen_years: set[int] = set()
    for index, hit in enumerate(hits, start=1):
        ticker = hit.chunk.ticker.upper()
        year = hit.year
        take = False
        if needed_tickers and ticker in needed_tickers and ticker not in seen_tickers:
            take = True
            seen_tickers.add(ticker)
        if needed_years and year is not None and year in needed_years and year not in seen_years:
            take = True
            seen_years.add(year)
        if not needed_tickers and not needed_years and not chosen:
            take = True
        if take:
            chosen.append((index, hit))
        have_tickers = not needed_tickers or needed_tickers <= seen_tickers
        have_years = not needed_years or needed_years <= seen_years
        if chosen and have_tickers and have_years:
            break
    if not chosen and hits:
        return [(1, hits[0])]
    return chosen


_GRAPH_HINTS = (
    "subsidiary",
    "subsidiaries",
    "rssd",
    "holding company",
    "parent company",
    "parent of",
    " lei",
)
_RSSD = re.compile(r"\bRSSD\s+(\d+)\b", re.IGNORECASE)


def _graph_call(question: str, traces: Sequence[TraceStep]) -> ToolCall | None:
    if any(step.tool == "graph_query" for step in traces):
        return None
    lowered = question.lower()
    if not any(hint in lowered for hint in _GRAPH_HINTS):
        return None
    if "parent" in lowered:
        kind = "parent"
    elif "filing" in lowered:
        kind = "filings"
    elif "path" in lowered:
        kind = "path"
    elif "cet1" in lowered or "ratio" in lowered or "metric" in lowered:
        kind = "metric"
    else:
        kind = "subsidiaries"
    args: dict[str, object] = {"kind": kind}
    tickers = sorted(tickers_named(question))
    if tickers:
        args["ticker"] = tickers[0]
    match = _RSSD.search(question)
    if match:
        args["rssd"] = match.group(1)
    if kind == "metric" and "cet1" in lowered:
        args["name"] = "CET1"
    years = sorted(years_named(question))
    if years and kind in {"metric", "filings"}:
        args["period"] = str(years[0])
    if kind == "filings":
        args["form"] = "10-K"
    if (
        kind in {"subsidiaries", "parent", "filings", "metric"}
        and "ticker" not in args
        and "rssd" not in args
    ):
        return None
    if kind == "path" and ("ticker" not in args or "rssd" not in args):
        return None
    return ToolCall(name="graph_query", args=args)


def _plan_system_prompt() -> str:
    schema = json.dumps(PlannerDecision.model_json_schema())
    return (
        "You plan tool calls for questions about U.S. bank filings. "
        "Available tools: search_filings (query, optional ticker, form, period, k), "
        "xbrl_fact (ticker, concept, optional period), "
        "fdic_bankfind (query), calculator (expression), "
        "graph_query (kind subsidiaries|parent|filings|metric|path, ticker or rssd). "
        "Use graph_query for holding-company structure, RSSD, LEI, or subsidiary joins. "
        "For a comparison or multi-hop question, search each bank and year separately. "
        "When you have enough retrieved passages, return kind synthesize and no calls. "
        "Return one JSON object. "
        f"JSON schema: {schema}"
    )


def _plan_user_prompt(
    question: str,
    traces: Sequence[TraceStep],
    hits: Sequence[Hit],
) -> str:
    lines = [f"Question: {question}", "Tool results so far:"]
    if not traces:
        lines.append("(none)")
    for step in traces:
        lines.append(_format_trace(step))
    lines.append(f"Retrieved passages: {len(hits)}")
    return "\n".join(lines)


_UNTRUSTED_TOOLS = frozenset({"xbrl_fact", "fdic_bankfind", "graph_query"})


def _format_trace(step: TraceStep) -> str:
    if step.tool in _UNTRUSTED_TOOLS:
        return (
            f"- {step.tool} ok={step.ok}\n"
            "<untrusted_tool_output>\n"
            f"{step.detail}\n"
            "</untrusted_tool_output>"
        )
    return f"- {step.tool} ok={step.ok} {step.detail}"


def _decision_from_content(content: str) -> PlannerDecision:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("planner content was not JSON") from exc
    try:
        return PlannerDecision.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _message_content(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ModelUnavailable("openai chat completion response was not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelUnavailable("openai chat completion response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ModelUnavailable("openai chat completion choice was not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ModelUnavailable("openai chat completion choice missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("openai chat completion content was not a string")
    return content
