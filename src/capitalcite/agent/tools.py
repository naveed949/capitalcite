"""Tool registry. Unknown names are denied. Issue #9 wraps execute later."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from capitalcite.agent.calculator import calculate
from capitalcite.agent.types import TOOL_NAMES, ToolResult
from capitalcite.retrieval.types import Hit, SearchFilters, parse_filters

ToolFn = Callable[[Mapping[str, object]], Awaitable[ToolResult]]


class Tool(Protocol):
    name: str

    async def run(self, args: Mapping[str, object]) -> ToolResult: ...


class SearchFilingsArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str = Field(min_length=1)
    ticker: str | None = None
    form: str | None = None
    period: str | None = None
    k: int | None = Field(default=None, ge=1, le=50)


class XbrlFactArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str = Field(min_length=1)
    concept: str = Field(min_length=1)
    period: str | None = None


class FdicBankfindArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str = Field(min_length=1)


class CalculatorArgs(BaseModel):
    model_config = ConfigDict(frozen=True)
    expression: str = Field(min_length=1)


class SearchFn(Protocol):
    async def __call__(
        self,
        query: str,
        *,
        k: int | None = None,
        filters: SearchFilters | None = None,
    ) -> list[Hit]: ...


class XbrlFn(Protocol):
    async def __call__(self, ticker: str, concept: str, period: str | None) -> str: ...


class FdicFn(Protocol):
    async def __call__(self, query: str) -> str: ...


class ToolRegistry:
    """name -> runner. execute() is the only way to call a tool."""

    def __init__(self, tools: Mapping[str, ToolFn]) -> None:
        self._tools = dict(tools)

    async def execute(self, name: str, args: Mapping[str, object]) -> ToolResult:
        runner = self._tools.get(name)
        if runner is None:
            return ToolResult(
                ok=False,
                tool=name,
                args=dict(args),
                detail="unknown tool",
            )
        try:
            return await runner(args)
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool=name,
                args=dict(args),
                detail=str(exc) or type(exc).__name__,
            )


def default_registry(
    *,
    search: SearchFn,
    xbrl: XbrlFn,
    fdic: FdicFn,
) -> ToolRegistry:
    """Four named tools. Callers inject HTTP and retrieval."""

    async def search_filings(args: Mapping[str, object]) -> ToolResult:
        try:
            parsed = SearchFilingsArgs.model_validate(args)
            filters = parse_filters(ticker=parsed.ticker, form=parsed.form, period=parsed.period)
        except (ValidationError, ValueError) as exc:
            return _invalid("search_filings", args, str(exc))
        hits = await search(parsed.query, k=parsed.k, filters=filters)
        tickers = sorted({hit.chunk.ticker for hit in hits if hit.chunk.ticker})
        detail = f"hits={len(hits)}"
        if tickers:
            detail += " tickers=" + ",".join(tickers)
        return ToolResult(
            ok=True,
            tool="search_filings",
            args=dict(args),
            detail=detail,
            hits=tuple(hits),
        )

    async def xbrl_fact(args: Mapping[str, object]) -> ToolResult:
        try:
            parsed = XbrlFactArgs.model_validate(args)
        except ValidationError as exc:
            return _invalid("xbrl_fact", args, str(exc))
        try:
            value = await xbrl(parsed.ticker, parsed.concept, parsed.period)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                tool="xbrl_fact",
                args=dict(args),
                detail=str(exc),
            )
        return ToolResult(
            ok=True,
            tool="xbrl_fact",
            args=dict(args),
            detail=value,
            value=value,
        )

    async def fdic_bankfind(args: Mapping[str, object]) -> ToolResult:
        try:
            parsed = FdicBankfindArgs.model_validate(args)
        except ValidationError as exc:
            return _invalid("fdic_bankfind", args, str(exc))
        try:
            value = await fdic(parsed.query)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                tool="fdic_bankfind",
                args=dict(args),
                detail=str(exc),
            )
        return ToolResult(
            ok=True,
            tool="fdic_bankfind",
            args=dict(args),
            detail=value,
            value=value,
        )

    async def calculator(args: Mapping[str, object]) -> ToolResult:
        try:
            parsed = CalculatorArgs.model_validate(args)
            value = calculate(parsed.expression)
        except (ValidationError, ValueError) as exc:
            return _invalid("calculator", args, str(exc))
        rendered = format(value, "f")
        return ToolResult(
            ok=True,
            tool="calculator",
            args=dict(args),
            detail=rendered,
            value=rendered,
        )

    return ToolRegistry(
        {
            "search_filings": search_filings,
            "xbrl_fact": xbrl_fact,
            "fdic_bankfind": fdic_bankfind,
            "calculator": calculator,
        }
    )


def _invalid(tool: str, args: Mapping[str, object], detail: str) -> ToolResult:
    return ToolResult(ok=False, tool=tool, args=dict(args), detail=detail)


assert set(TOOL_NAMES) == {
    "search_filings",
    "xbrl_fact",
    "fdic_bankfind",
    "calculator",
}
