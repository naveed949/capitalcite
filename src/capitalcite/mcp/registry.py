"""MCP ToolRegistry. Only the two advertised names. Shares search_filings with the agent."""

from __future__ import annotations

from capitalcite.agent.tools import SearchFn, ToolFn, ToolRegistry, make_search_filings
from capitalcite.gate import BrokenPolicy, Policy
from capitalcite.mcp.names import MCP_TOOL_NAMES


def mcp_registry(*, search: SearchFn, ask_runner: ToolFn) -> ToolRegistry:
    return ToolRegistry(
        {
            "search_filings": make_search_filings(search),
            "ask": ask_runner,
        }
    )


def mcp_policy(loaded: Policy | BrokenPolicy) -> Policy | BrokenPolicy:
    """Keep ask and search_filings. Drop agent-only names so MCP cannot run them."""
    if isinstance(loaded, BrokenPolicy):
        return loaded
    return Policy(
        version=loaded.version,
        default="deny",
        tools={name: rule for name, rule in loaded.tools.items() if name in MCP_TOOL_NAMES},
    )
