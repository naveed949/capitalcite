from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from capitalcite.audit import AuditRecord, verify
from capitalcite.trace.types import SpanRecord

_ENV_KEYS = (
    "SEC_USER_AGENT",
    "QDRANT_PATH",
    "AUDIT_LOG_PATH",
    "CAPITALCITE_TRACE_FILE",
    "ASK_MODEL",
    "EMBEDDER",
    "LANGFUSE_TRACING_ENABLED",
    "LOG_LEVEL",
    "APP_ENV",
    "CHUNKER",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "QDRANT_COLLECTION",
    "NO_COLOR",
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "VIRTUAL_ENV",
    "UV_CACHE_DIR",
    "UV_PROJECT",
    "HF_HOME",
    "FASTEMBED_CACHE_PATH",
    "XDG_CACHE_HOME",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="stdio MCP client proof for capitalcite mcp")
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    art = args.artifacts
    art.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(drive(art, args.root.resolve()))
    except Exception as exc:
        sys.stderr.write(f"verify: mcp probe failed: {exc}\n")
        return 1
    return 0


def _body(result: object) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError(f"tool result had no JSON body: {result!r}")


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    missing = [key for key in ("SEC_USER_AGENT", "QDRANT_PATH", "AUDIT_LOG_PATH") if key not in env]
    if missing:
        raise RuntimeError("missing env: " + ", ".join(missing))
    env.setdefault("ASK_MODEL", "fake")
    env.setdefault("LANGFUSE_TRACING_ENABLED", "false")
    env.setdefault("LOG_LEVEL", "WARNING")
    return env


def _write(art: Path, name: str, body: dict[str, Any]) -> None:
    (art / name).write_text(json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8")


async def drive(art: Path, root: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not on PATH")
    params = StdioServerParameters(
        command=uv,
        args=["run", "--frozen", "capitalcite", "mcp"],
        env=_env(),
        cwd=str(root),
    )
    async with Client(params, read_timeout_seconds=180) as client:
        listed = await client.list_tools()
        names = sorted(tool.name for tool in listed.tools)
        _write(art, "mcp.tools.json", {"tools": names})
        if set(names) != {"ask", "search_filings"}:
            raise RuntimeError(f"listed tools {names}")

        search = await client.call_tool("search_filings", {"query": "CET1 ratio", "ticker": "JPM"})
        search_body = _body(search)
        _write(art, "mcp.search.body.json", search_body)
        if search_body.get("ok") is not True:
            raise RuntimeError(f"search_filings denied: {search_body}")
        hits = search_body.get("hits")
        if not isinstance(hits, list) or not hits:
            raise RuntimeError("search_filings returned no hits")
        chunk = hits[0].get("chunk") if isinstance(hits[0], dict) else None
        if not isinstance(chunk, dict) or not chunk.get("doc_id") or "source_url" not in chunk:
            raise RuntimeError("search_filings hit missing citation fields")

        extra = await client.call_tool("search_filings", {"query": "CET1 ratio", "cmd": "ls"})
        extra_body = _body(extra)
        _write(art, "mcp.search.extra.body.json", extra_body)
        if extra_body.get("ok") is not False or extra_body.get("reason") != "invalid_args":
            raise RuntimeError(f"extra keys were not denied: {extra_body}")

        unknown = await client.call_tool("calculator", {"expression": "1+1"})
        unknown_body = _body(unknown)
        _write(art, "mcp.calculator.body.json", unknown_body)
        if unknown_body.get("ok") is not False or unknown_body.get("reason") != "unknown_tool":
            raise RuntimeError(f"calculator was not unknown_tool: {unknown_body}")

        asked = await client.call_tool(
            "ask",
            {"question": "What was the CET1 ratio?", "ticker": "JPM", "model": "fake"},
        )
        ask_body = _body(asked)
        _write(art, "mcp.ask.body.json", ask_body)
        if ask_body.get("ok") is not True:
            raise RuntimeError(f"ask failed: {ask_body}")
        if ask_body.get("kind") != "answered":
            raise RuntimeError(f"ask did not answer: {ask_body}")
        citations = ask_body.get("citations")
        if not isinstance(citations, list) or not citations:
            raise RuntimeError("answered ask missing citations")
        cite = citations[0]
        if not isinstance(cite, dict) or not cite.get("doc_id") or not cite.get("quote"):
            raise RuntimeError(f"citation missing fields: {cite}")

    log_path = Path(os.environ["AUDIT_LOG_PATH"])
    report = verify(log_path)
    if not report.ok:
        raise RuntimeError(f"audit verify failed: {report}")
    if report.records < 4:
        raise RuntimeError(f"expected >=4 audit records, got {report.records}")
    records = [
        AuditRecord.model_validate_json(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    extra_record = next(
        item for item in records if item.tool == "search_filings" and item.decision == "denied"
    )
    if extra_record.reason != "invalid_args":
        raise RuntimeError(f"extra-key audit reason={extra_record.reason}")
    calc_record = next(item for item in records if item.tool == "calculator")
    if calc_record.decision != "denied" or calc_record.reason != "unknown_tool":
        raise RuntimeError("calculator audit was not unknown_tool")
    ask_record = next(item for item in records if item.tool == "ask")
    if ask_record.decision != "allowed":
        raise RuntimeError("ask was not allowed in the audit log")

    spans_path = Path(os.environ["CAPITALCITE_TRACE_FILE"])
    if not spans_path.is_file():
        raise RuntimeError(f"missing span file {spans_path}")
    span_names: set[str] = set()
    for line in spans_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        span_names.add(SpanRecord.model_validate_json(line).name)
    for required in ("gate", "ask", "retrieve"):
        if required not in span_names:
            raise RuntimeError(f"span file missing {required}: {sorted(span_names)}")
    print(f"mcp probe ok tools={names} audit_records={report.records} spans={sorted(span_names)}")


if __name__ == "__main__":
    raise SystemExit(main())
