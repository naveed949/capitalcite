# 0015. MCP server

Date: 2026-10-16

Status: Accepted

Issue #15 asks to expose `search_filings` and `ask` as MCP tools, routed
through the same fail-closed gate and audit log, and to prove a real MCP
client can call them. This record is why MCP is a stdio transport over
`AuditingTools`, why `ask` is a policy tool that the agent registry does
not run, and why the server overrides `call_tool` so extra keys still
reach `Gate.decide`. Number 0012 is Azure Container Apps. Number 0013 is
PR #31 (`capitalcite check`). Number 0014 is PR #34 (optional Neo4j
entity graph).

## MCP is a transport. The gate is still the only way to run a tool.

An IDE host launches `capitalcite mcp` as a child and speaks JSON-RPC on
stdio. That host is untrusted in the same way the planner is. It chooses
tool names and argument objects. Calling `retrieve()` or `ask()` from an
MCP handler would be a second ungated path. ADR 0009 already rejected a
second path around `execute`.

Each MCP `tools/call` constructs a new `Gate` (per-call budgets, the same
rule as one HTTP agent request) and `AuditingTools.execute`. A deny never
calls the runner. A failed audit write is `audit unavailable`. `ask()`
still owns grounding: the ask runner retrieves, then `ask()` /
`ground()` / `guard()`. Inner retrieve on MCP ask is the same ungated
retrieve HTTP `path=rag` already uses. The MCP `ask` call itself is the
gated user action.

`path=agent` stays on `POST /ask`. Nesting LangGraph under MCP would
nest two Gates. An IDE can call `search_filings` more than once, then
`ask`.

## Hosts discover tools. REST stays for services.

An MCP host calls `list_tools` and receives names plus JSON Schema. That
is native discovery: Cursor, Claude Desktop, and other hosts do not curl
OpenAPI. REST stays for services that already speak HTTP — uvicorn,
compose, curl, and any caller of `POST /search` or `POST /ask`. MCP does
not replace those routes. This PR does not change them.

## Two advertised names. One policy file.

The shipped allow-list gains `ask`. Agent `TOOL_NAMES` stays four names.
A test fails unless `policy.tools == TOOL_NAMES ∪ {ask}`. The MCP session
filters that file to `search_filings` and `ask` before `decide()`, so
`calculator` over MCP is `unknown_tool`. The agent registry still does
not contain `ask`. A planner that names `ask` gets `unknown tool` from
`ToolRegistry` and never calls `ask()`.

The official `mcp` v2 SDK validates tool arguments against type hints and
drops extra keys before the handler runs. That would turn
`{"query":"x","cmd":"ls"}` into an allow. `CapitalciteMCP` overrides
`call_tool` and `list_tools`. Advertised schemas are the JSON Schema
already on the policy rules. The raw `arguments` object is what
`execute` sees. Extra keys stay `invalid_args`.

stdio stdout is the wire. Logging for this command is stderr. `mcp` is a
main dependency so `uv run capitalcite mcp` works in verify without an
optional extra.

## stdio now. Streamable HTTP later, and not without auth.

stdio is a child process. The host launches `capitalcite mcp`. stdout is
the JSON-RPC wire. There is no listening socket, so local attach does not
need a second auth layer. Logs go to stderr so a stray print cannot
corrupt a message.

Streamable HTTP would put the same tools on a URL. That is a network
trust boundary the gate does not cover: anyone who can POST can name a
tool. Auth (a token, mTLS, or an allow-listed reverse proxy) has to exist
before that port is reachable. This PR does not add it. HTTP transport is
deferred.

## CLI is the user path. Pytest is not verification.

`capitalcite mcp` blocks on stdio. Tests use `Client(server)` in-process,
no network, FakeEmbedder, `:memory:` Qdrant. `scripts/verify.sh` launches
the same command with `Client(StdioServerParameters)` and checks the
audit JSONL plus span JSONL. Live Langfuse UI stays UNVERIFIED without
`:3000`.

HTTP `POST /search` and `POST /ask` do not change.

## Alternatives

Wrapping FastAPI routes would couple MCP to HTTP status codes and would
still need a Gate around `/search`, which ADR 0009 left ungated on
purpose.

Exposing every agent tool would put `calculator` and live XBRL on any
IDE that attached the server. The issue named two tools.

A second MCP policy file would drift from `tools.json`.
