from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, Tool

from capitalcite.audit import AuditLog
from capitalcite.config import Settings, get_settings
from capitalcite.logging import configure_logging
from capitalcite.mcp.session import McpSession, advertised_tools
from capitalcite.retrieval import create_retrieval_setup
from capitalcite.trace import configure_settings, shutdown


class CapitalciteMCP(MCPServer):
    def __init__(self, session: McpSession) -> None:
        super().__init__(
            "capitalcite",
            instructions=(
                "Cited answers over U.S. bank filings. search_filings, metric_query, "
                "and ask go through the fail-closed gate and the audit log."
            ),
            version=version("capitalcite"),
        )
        self._session = session

    async def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=name,
                description=description,
                input_schema=schema,
            )
            for name, description, schema in advertised_tools(self._session)
        ]

    def _tool_input_schema(self, name: str) -> dict[str, Any] | None:
        for tool_name, _description, schema in advertised_tools(self._session):
            if tool_name == name:
                return schema
        return None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any = None,
    ) -> CallToolResult:
        payload = await self._session.dispatch(
            name, arguments if isinstance(arguments, Mapping) else {}
        )
        text = json.dumps(payload, sort_keys=True, default=str)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=payload,
            is_error=False,
        )


def build_server(session: McpSession) -> CapitalciteMCP:
    return CapitalciteMCP(session)


def session_from_settings(settings: Settings) -> McpSession:
    setup = create_retrieval_setup(settings)
    return McpSession(settings=settings, setup=setup, log=AuditLog(settings.audit_log_path))


def run_stdio() -> None:
    """Block on stdin/stdout. stdout is the MCP wire."""
    settings = get_settings()
    configure_logging(settings.log_level, stream=sys.stderr)
    configure_settings(settings)
    session = session_from_settings(settings)
    server = build_server(session)
    try:
        server.run()
    finally:
        shutdown()
        _close_setup(session)


def _close_setup(session: McpSession) -> None:
    import asyncio

    try:
        asyncio.run(session.setup.aclose())
    except RuntimeError:
        return
