# MCP server

`capitalcite mcp` speaks the Model Context Protocol on stdio. An MCP
client lists `search_filings` and `ask`. Both calls go through the
fail-closed gate, the append-only audit log, and `ask()` / `ground()`
for answers. stdout is the protocol wire. Logs go to stderr.

## Sub-features

- `mcp-help` lists the `mcp` command and says stdio is the wire.
- `mcp-list` advertises only `search_filings` and `ask`.
- `mcp-search` returns citation fields from indexed chunks.
- `mcp-invalid-args` denies extra keys on `search_filings` with `invalid_args`. The runner is not called.
- `mcp-unknown` denies `calculator` with `unknown_tool`.
- `mcp-ask` answers with grounded citations when `model` is `fake`, or refuses.
- `mcp-audit` appends one JSONL record per tools/call. `capitalcite audit verify` exits 0 on that file.
- `mcp-spans` writes `gate`, `ask`, and `retrieve` records to `CAPITALCITE_TRACE_FILE`. Live Langfuse UI is UNVERIFIED without `:3000`.

## How to get to it (user POV)

- Run `uv run capitalcite mcp`. A host launches that command as a child and speaks JSON-RPC on stdio.
- Point `QDRANT_PATH` at an indexed store. Point `AUDIT_LOG_PATH` and `CAPITALCITE_TRACE_FILE` at files this run owns.
- `ASK_MODEL=fake` answers from retrieved chunks and does not read `OPENAI_API_KEY`.

## Driving it with the capitalcite CLI and an MCP client

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `capitalcite chunk` has written `chunks.jsonl` under an output dir this run owns (the fixture corpus is enough).
- `capitalcite index --input-dir <that dir>` has built an embedded Qdrant store at a `QDRANT_PATH` this run owns.
- Doctor: `uv run capitalcite mcp --help` exits 0.
- This path does not need uvicorn, Docker, live EDGAR, or `OPENAI_API_KEY`.

- **Show help.** Run `uv run capitalcite mcp --help`. Exit code is `0`. Stdout mentions stdio.
- **List tools.** Launch `uv run capitalcite mcp` with `Client(StdioServerParameters)`. `list_tools` returns `ask` and `search_filings` only.
- **Search.** Call `search_filings` with `{"query":"CET1 ratio","ticker":"JPM"}`. Structured content `ok` is true. Hits include `chunk.doc_id` and `chunk.source_url`.
- **Deny extra keys.** Call `search_filings` with `{"query":"CET1 ratio","cmd":"ls"}`. `ok` is false and `reason` is `invalid_args`.
- **Deny an agent-only tool.** Call `calculator` with `{"expression":"1+1"}`. `reason` is `unknown_tool`.
- **Ask.** Call `ask` with `{"question":"What was the CET1 ratio?","ticker":"JPM","model":"fake"}`. `kind` is `answered` and citations include `doc_id` and `quote`.
- **Audit.** After those calls, `uv run capitalcite audit verify --path $AUDIT_LOG_PATH` exits 0. Records include an allowed `search_filings`, a denied extra-key call, a denied `calculator`, and an allowed `ask`.
- **Spans.** `CAPITALCITE_TRACE_FILE` is JSONL with `gate`, `ask`, and `retrieve` names.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `mcp.help.out`, `mcp.tools.json`, `mcp.search.body.json`, `mcp.search.extra.body.json`, `mcp.calculator.body.json`, `mcp.ask.body.json`, `mcp.audit.out`, and `mcp.spans.jsonl`.
- **Proof.** Keep the MCP JSON bodies, the audit JSONL, and the span JSONL. Pytest `Client(server)` is not this feature.

## Gotchas

- stdout is JSON-RPC. Logging for this command is stderr. Do not print to stdout from the server.
- `StdioServerParameters.env` is merged onto a small default allow-list. Pass `SEC_USER_AGENT`, `QDRANT_PATH`, and `AUDIT_LOG_PATH` explicitly.
- Isolate `AUDIT_LOG_PATH` from the live agent log so MCP records do not change `audit.agent` counts.
- `POST /search` and `POST /ask` are a different feature. Do not treat curl as MCP proof.
- `CliRunner().invoke(...)` and in-process `Client(server)` in pytest are not this feature.
- `path=agent` stays on HTTP. MCP `ask` is RAG retrieve then `ask()`.
- Live Langfuse on `:3000` is UNVERIFIED when compose is down. The file exporter is the proof.
