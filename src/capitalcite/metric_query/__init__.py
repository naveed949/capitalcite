"""metric_query shell. Siblings do not import this module."""

from collections.abc import Mapping

from capitalcite.agent.types import ToolResult
from capitalcite.metric_query.cite import CitationResolver, hits_for
from capitalcite.metric_query.query import (
    MetricCell,
    MetricQuery,
    MetricQueryError,
    QueryBackend,
    cells_equal,
    parse_metric_query,
    render_figure,
)

__all__ = [
    "MetricCell",
    "MetricQuery",
    "QueryBackend",
    "cells_equal",
    "parse_metric_query",
    "run_metric_query",
]


def run_metric_query(
    args: Mapping[str, object],
    *,
    backend: QueryBackend,
    citations: CitationResolver,
) -> ToolResult:
    """Parse, execute, resolve, and build hits.

    ValueError and MetricQueryError become ok False. A null cell, a missing
    fact row, or a missing accession returns ok False with empty hits. The
    failure detail does not include the rendered figure. Success sets hits,
    value, and detail to that figure. args are the mapping the caller sent.
    """
    recorded = dict(args) if isinstance(args, Mapping) else {}
    figure: str | None = None
    try:
        query = parse_metric_query(args)
        cell = backend.execute(query)
        if cell.value is None:
            return _failed(recorded, "metric cell is null")
        figure = render_figure(cell.value)
        hits = hits_for(cell, citations.resolve(cell))
    except (ValueError, MetricQueryError) as exc:
        detail = str(exc).strip() or type(exc).__name__
        if figure is not None and figure in detail:
            detail = "metric query failed"
        return _failed(recorded, detail)
    return ToolResult(
        ok=True,
        tool="metric_query",
        args=recorded,
        detail=figure,
        value=figure,
        hits=hits,
    )


def _failed(args: dict[str, object], detail: str) -> ToolResult:
    return ToolResult(ok=False, tool="metric_query", args=args, detail=detail, hits=())
