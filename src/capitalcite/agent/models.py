"""Planner + synthesizer adapters. Fake is deterministic. OpenAI uses json_object."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date
from typing import NamedTuple, Protocol

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
from capitalcite.metric_query.query import GroupBy, MetricName
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
        metric_calls = _metric_calls(question, traces)
        if metric_calls is not None:
            if metric_calls:
                return PlannerDecision(kind="plan", calls=tuple(metric_calls))
            return PlannerDecision(
                kind="synthesize",
                refusal_reason=_metric_periods(question).reason,
            )
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


_METRIC_PHRASES: tuple[tuple[str, MetricName], ...] = (
    ("holding company net income", MetricName.HOLDING_COMPANY_NET_INCOME),
    ("holding company deposits", MetricName.HOLDING_COMPANY_DEPOSITS),
    ("holding company assets", MetricName.HOLDING_COMPANY_ASSETS),
    ("net interest margin", MetricName.NIM),
    ("efficiency ratio", MetricName.EFFICIENCY_RATIO),
    ("deposits growth", MetricName.DEPOSITS_QOQ_GROWTH),
    ("deposit growth", MetricName.DEPOSITS_QOQ_GROWTH),
    ("qoq growth", MetricName.DEPOSITS_QOQ_GROWTH),
    ("total deposits", MetricName.TOTAL_DEPOSITS),
    ("total assets", MetricName.TOTAL_ASSETS),
    ("net income", MetricName.NET_INCOME),
    ("deposits", MetricName.TOTAL_DEPOSITS),
    ("assets", MetricName.TOTAL_ASSETS),
    ("nim", MetricName.NIM),
)
_QUARTER_END = re.compile(r"\b(\d{4})-(03-31|06-30|09-30|12-31)\b")
_QUARTER_NAME = re.compile(r"\bQ([1-4])\s+(\d{4})\b", re.IGNORECASE)
_COMPACT_QUARTER = re.compile(r"\b(\d{4})Q([1-4])\b", re.IGNORECASE)
_NUMERIC_ASK = re.compile(
    r"\b(?:what\s+(?:was|were|is|are)|how\s+much|value\s+of)\b", re.IGNORECASE
)
_NARRATIVE_ASK = re.compile(r"\b(?:describe|discuss|explain|strategy|risk)\b", re.IGNORECASE)
_HOLDING_WORD = re.compile(r"\b(?:holding\s+company|bank\s+holding|bhc|parent)\b", re.IGNORECASE)
_HOLDING_ENTITY = r"(?:holding\s+company|bank\s+holding\s+company|bhc|parent(?:\s+company)?)"
_METRIC_NOUN = r"(?:(?:total\s+)?(?:deposits|assets)|net\s+income)"
_HOLDING_PREFIX = re.compile(rf"\b{_HOLDING_ENTITY}(?:'s)?\s+{_METRIC_NOUN}\b", re.IGNORECASE)
_HOLDING_OF = re.compile(
    rf"\b{_METRIC_NOUN}\s+(?:of|for|at)\s+the\s+{_HOLDING_ENTITY}(?:\s+level)?\b",
    re.IGNORECASE,
)
_HOLDING_LEVEL = re.compile(r"\bat\s+the\s+(?:bank\s+)?holding\s+company\s+level\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_QUARTER_MONTH_DAYS = frozenset({(3, 31), (6, 30), (9, 30), (12, 31)})
_NAMED_QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_FLOW_METRICS = frozenset({MetricName.NET_INCOME, MetricName.HOLDING_COMPANY_NET_INCOME})
_HOLDING_METRIC = {
    MetricName.TOTAL_ASSETS: MetricName.HOLDING_COMPANY_ASSETS,
    MetricName.TOTAL_DEPOSITS: MetricName.HOLDING_COMPANY_DEPOSITS,
    MetricName.NET_INCOME: MetricName.HOLDING_COMPANY_NET_INCOME,
}


class _PeriodAsk(NamedTuple):
    periods: tuple[str, ...]
    year_only: bool
    reason: str = ""


def _metric_phrase(question: str) -> MetricName | None:
    lowered = question.lower()
    best: tuple[int, MetricName] | None = None
    for phrase, metric in _METRIC_PHRASES:
        if phrase == "nim":
            if re.search(r"\bnim\b", lowered) is None:
                continue
        elif phrase not in lowered:
            continue
        length = len(phrase)
        if best is None or length > best[0]:
            best = (length, metric)
    if best is None:
        return None
    return best[1]


def _holding_modifies_metric(question: str) -> bool:
    return (
        _HOLDING_PREFIX.search(question) is not None
        or _HOLDING_OF.search(question) is not None
        or _HOLDING_LEVEL.search(question) is not None
    )


def _invalid_period_reason(question: str) -> str:
    for match in _ISO_DATE.finditer(question):
        year, month, day_number = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        try:
            day = date(year, month, day_number)
        except ValueError:
            return "period is not a valid date"
        if (day.month, day.day) not in _QUARTER_MONTH_DAYS:
            return "period is not a quarter-end"
    return ""


def _metric_periods(question: str) -> _PeriodAsk:
    reason = _invalid_period_reason(question)
    if reason:
        return _PeriodAsk((), False, reason)
    found: list[date] = []
    covered: set[int] = set()
    named_quarter = False
    for match in _QUARTER_END.finditer(question):
        day = date.fromisoformat(match.group(0))
        if day not in found:
            found.append(day)
        covered.add(day.year)
        named_quarter = True
    for match in _QUARTER_NAME.finditer(question):
        quarter = int(match.group(1))
        year = int(match.group(2))
        month, day_number = _NAMED_QUARTER_END[quarter]
        day = date(year, month, day_number)
        if day not in found:
            found.append(day)
        covered.add(year)
        named_quarter = True
    for match in _COMPACT_QUARTER.finditer(question):
        year = int(match.group(1))
        quarter = int(match.group(2))
        month, day_number = _NAMED_QUARTER_END[quarter]
        day = date(year, month, day_number)
        if day not in found:
            found.append(day)
        covered.add(year)
        named_quarter = True
    if named_quarter:
        return _PeriodAsk(tuple(day.isoformat() for day in found), False)
    for year in sorted(years_named(question)):
        if year in covered:
            continue
        found.append(date(year, 12, 31))
        covered.add(year)
    return _PeriodAsk(tuple(day.isoformat() for day in found), bool(found))


def _metric_calls(question: str, traces: Sequence[TraceStep]) -> list[ToolCall] | None:
    if _NARRATIVE_ASK.search(question) is not None:
        return None
    if _NUMERIC_ASK.search(question) is None:
        return None
    metric = _metric_phrase(question)
    if metric is None:
        return None
    if _holding_modifies_metric(question):
        metric = _HOLDING_METRIC.get(metric, metric)
    elif _HOLDING_WORD.search(question) is not None:
        return None
    tickers = sorted(tickers_named(question))
    if not tickers:
        return None
    asked = _metric_periods(question)
    if asked.reason:
        return []
    periods = list(asked.periods)
    if not periods:
        return None
    if asked.year_only and metric in _FLOW_METRICS:
        return None
    done = {
        (str(step.args.get("ticker") or "").upper(), str(step.args.get("period") or ""))
        for step in traces
        if step.tool == "metric_query"
    }
    calls: list[ToolCall] = []
    for ticker in tickers:
        for period in periods:
            if (ticker, period) in done:
                continue
            calls.append(
                ToolCall(
                    name="metric_query",
                    args={
                        "metric": metric.value,
                        "group_by": GroupBy.QUARTER_AND_TICKER.value,
                        "ticker": ticker,
                        "period": period,
                    },
                )
            )
    return calls


def _hits_for_question(question: str, hits: Sequence[Hit]) -> list[tuple[int, Hit]]:
    metric_sections = frozenset(metric.value for metric in MetricName)
    chosen: list[tuple[int, Hit]] = []
    for index, hit in enumerate(hits, start=1):
        if hit.chunk.section in metric_sections:
            chosen.append((index, hit))
    filing = [
        (index, hit)
        for index, hit in enumerate(hits, start=1)
        if hit.chunk.section not in metric_sections
    ]
    chosen.extend(_pick_filing_hits(question, filing))
    if not chosen and hits:
        return [(1, hits[0])]
    return chosen


def _pick_filing_hits(question: str, indexed: Sequence[tuple[int, Hit]]) -> list[tuple[int, Hit]]:
    needed_tickers = tickers_named(question)
    needed_years = years_named(question)
    chosen: list[tuple[int, Hit]] = []
    seen_tickers: set[str] = set()
    seen_years: set[int] = set()
    for index, hit in indexed:
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
        "graph_query (kind subsidiaries|parent|filings|metric|path, ticker or rssd), "
        "metric_query (metric, group_by, ticker, period, optional entity_level). "
        "Use metric_query for a numeric ask (what was/were/is/are, how much, value of) "
        "of one governed metric, one ticker, and one named quarter-end. "
        "ticker must be uppercase and match ^[A-Z][A-Z0-9]{0,5}$. "
        "Do not use it for describe, discuss, explain, strategy, or risk wording, "
        "or for year-only net_income. "
        "Use a holding_company_ metric only when a holding word directly modifies "
        "the metric (BHC total assets; assets for the holding company; "
        "at the holding company level). Any other mention of holding company, "
        "BHC, or parent is search_filings. "
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
