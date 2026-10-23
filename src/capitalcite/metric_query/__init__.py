from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from capitalcite.agent.types import ToolResult

from capitalcite.metric_query.backends import CatalogSql, LiveMetricFlow, load_catalog
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
    "open_metric_query",
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
    from capitalcite.agent.types import ToolResult

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
    from capitalcite.agent.types import ToolResult

    return ToolResult(ok=False, tool="metric_query", args=args, detail=detail, hits=())


def open_metric_query(
    *,
    backend_name: Literal["catalog", "live"],
    warehouse: Path,
    catalog_path: Path | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[QueryBackend, CitationResolver]:
    """Process start. catalog requires catalog_path and warehouse.

    live requires cwd, env with DBT_TARGET, and warehouse for citations.
    Missing files raise MetricQueryError. Does not open a shared connection.
    """
    if not warehouse.is_file():
        raise MetricQueryError("warehouse is missing")
    citations = CitationResolver(warehouse)
    if backend_name == "catalog":
        if catalog_path is None or not catalog_path.is_file():
            raise MetricQueryError("catalog is missing")
        return CatalogSql(load_catalog(catalog_path), warehouse), citations
    if backend_name == "live":
        if (
            cwd is None
            or not cwd.is_dir()
            or env is None
            or not str(env.get("DBT_TARGET") or "").strip()
        ):
            raise MetricQueryError("live metric query is not configured")
        return LiveMetricFlow(cwd=cwd, env=env), citations
    raise MetricQueryError("unknown metric backend")
