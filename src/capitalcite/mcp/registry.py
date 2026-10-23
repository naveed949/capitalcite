from __future__ import annotations

from typing import Any

from capitalcite.agent.tools import (
    SearchFn,
    ToolFn,
    ToolRegistry,
    make_metric_query,
    make_search_filings,
    unconfigured_metric_query,
)
from capitalcite.gate import BrokenPolicy, Policy
from capitalcite.mcp.names import MCP_TOOL_NAMES
from capitalcite.metric_query import QueryBackend


def mcp_registry(
    *,
    search: SearchFn,
    ask_runner: ToolFn,
    metric_backend: QueryBackend | None = None,
    metric_citations: Any = None,
) -> ToolRegistry:
    metric_query: ToolFn
    if metric_backend is None or metric_citations is None:
        metric_query = unconfigured_metric_query
    else:
        metric_query = make_metric_query(metric_backend, metric_citations)
    return ToolRegistry(
        {
            "search_filings": make_search_filings(search),
            "ask": ask_runner,
            "metric_query": metric_query,
        }
    )


def mcp_policy(loaded: Policy | BrokenPolicy) -> Policy | BrokenPolicy:
    if isinstance(loaded, BrokenPolicy):
        return loaded
    return Policy(
        version=loaded.version,
        default="deny",
        tools={name: rule for name, rule in loaded.tools.items() if name in MCP_TOOL_NAMES},
    )
