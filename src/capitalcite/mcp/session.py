from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from capitalcite.agent.audited import AuditingTools, request_trace_id
from capitalcite.agent.tools import SearchFn
from capitalcite.agent.types import ToolResult
from capitalcite.audit import AuditLog
from capitalcite.config import Settings
from capitalcite.gate import BrokenPolicy, Gate, load_policy
from capitalcite.mcp.ask_tool import hits_payload, make_ask_runner
from capitalcite.mcp.names import MCP_TOOL_NAMES
from capitalcite.mcp.registry import mcp_policy, mcp_registry
from capitalcite.retrieval import RetrievalMode, RetrievalSetup, retrieve
from capitalcite.retrieval.types import Hit, SearchFilters
from capitalcite.trace import ObservationKind, ask_attrs, observation, tool_attrs
from capitalcite.trace.types import AttrValue


class McpSession:
    def __init__(
        self,
        *,
        settings: Settings,
        setup: RetrievalSetup,
        log: AuditLog,
    ) -> None:
        self.settings = settings
        self.setup = setup
        self.log = log
        self.policy = mcp_policy(load_policy(settings.gate_policy))
        search = _bound_search(setup)
        self.registry = mcp_registry(
            search=search,
            ask_runner=make_ask_runner(search, setup, settings),
        )

    async def dispatch(self, name: str, args: Mapping[str, object]) -> dict[str, object]:
        recorded = dict(args) if isinstance(args, Mapping) else {"repr": repr(args)}
        if name == "ask":
            kind = ObservationKind.ASK
            span_name = "ask"
            attrs: dict[str, AttrValue] = ask_attrs(
                path="rag",
                question=str(recorded.get("question") or ""),
            )
        else:
            kind = ObservationKind.TOOL
            span_name = name
            attrs = tool_attrs(tool=name, ok=False)
        with observation(span_name, kind, **attrs) as span:
            tools = AuditingTools(
                Gate(self.policy, self.registry),
                self.log,
                request_trace_id(),
            )
            result = await tools.execute(name, recorded)
            payload = serialize_result(result)
            if name == "ask":
                refusal = result.reason
                if refusal is None:
                    raw_reason = payload.get("refusal_reason")
                    refusal = raw_reason if isinstance(raw_reason, str) else None
                span.set(
                    **ask_attrs(
                        path="rag",
                        question=str(recorded.get("question") or ""),
                        outcome=str(
                            payload.get("kind") or ("denied" if not result.ok else "answered")
                        ),
                        refusal_reason=refusal,
                    )
                )
            else:
                span.set(**tool_attrs(tool=result.tool, ok=result.ok))
            return payload


def serialize_result(result: ToolResult) -> dict[str, object]:
    if not result.ok:
        body: dict[str, object] = {
            "ok": False,
            "tool": result.tool,
            "detail": result.detail,
        }
        if result.reason is not None:
            body["reason"] = str(result.reason)
        return body
    if result.tool == "search_filings":
        return {
            "ok": True,
            "detail": result.detail,
            "hits": hits_payload(result.hits),
        }
    if result.tool == "ask":
        try:
            parsed = json.loads(result.detail)
        except json.JSONDecodeError:
            return {"ok": True, "tool": "ask", "detail": result.detail}
        if isinstance(parsed, dict):
            return parsed
        return {"ok": True, "tool": "ask", "detail": result.detail}
    return {"ok": True, "tool": result.tool, "detail": result.detail}


def advertised_tools(session: McpSession) -> list[tuple[str, str, dict[str, Any]]]:
    descriptions = {
        "search_filings": "Search indexed bank filings. Citations come from stored chunks.",
        "ask": "Answer a question with grounded citations, or refuse.",
    }
    if isinstance(session.policy, BrokenPolicy):
        return [
            (name, descriptions[name], {"type": "object", "additionalProperties": True})
            for name in sorted(MCP_TOOL_NAMES)
        ]
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for name in sorted(MCP_TOOL_NAMES):
        rule = session.policy.tools.get(name)
        schema: dict[str, Any] = dict(rule.args) if rule is not None else {"type": "object"}
        rows.append((name, descriptions[name], schema))
    return rows


def _bound_search(setup: RetrievalSetup) -> SearchFn:
    async def search(
        query: str,
        *,
        k: int | None = None,
        filters: SearchFilters | None = None,
    ) -> list[Hit]:
        return await retrieve(
            setup,
            query,
            mode=RetrievalMode.HYBRID,
            k=k,
            filters=filters,
        )

    return search
