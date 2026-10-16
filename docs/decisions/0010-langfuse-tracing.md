# 0010. Langfuse tracing

Date: 2026-10-16

Status: Accepted

Issue #11 asks for a trace tree on every `/ask` call: retrieval, each
LLM call, each tool call, and gate decisions, with cost and latency on
those spans. Eval runs must carry the commit SHA and the index config
hash. This record is why tracing is a no-op unless keys are present, why
tests use an in-memory exporter instead of Langfuse, and what a RAG
trace is allowed to contain.

## Traces, spans, and generations

A trace is one user-visible job. `POST /ask` is one trace. `capitalcite
eval` is one trace. A span is a timed step inside that job. A generation
is a span that called a model. Langfuse names the same objects
observation types: chain, retriever, generation, tool, guardrail,
evaluator, agent.

The first observation starts the trace. Children inherit the parent
through a context variable. Callers wrap work in `observation()`. They
do not pass Langfuse ids around. They do not import the Langfuse SDK.

## Optional, and never in the request path's failure budget

ADR 0001 declared Langfuse settings and a compose stack. It did not
import the SDK. `/health` still must not dial Langfuse. CI does not run
compose. A laptop can run `ASK_MODEL=fake` with embedded Qdrant and no
Docker.

Tracing is on only when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
are set and `LANGFUSE_TRACING_ENABLED` is not false. Missing keys is a
no-op, not a 503. An exporter exception is a no-op, not a 500. The
Langfuse constructor is lazy. Tests set `LANGFUSE_TRACING_ENABLED=false`
so a developer `.env` copied from `.env.example` cannot open
localhost:3000 during pytest.

`conftest.py` already blocks httpx. The Langfuse SDK exports OTLP, not
httpx. That is why tests never construct the live exporter. They install
a memory exporter. `verify.sh` can write the same spans to a JSONL file
without a UI.

Compose is pinned at Langfuse 4.59.0. The Python SDK is v5, which
requires that server version. `LANGFUSE_HOST` is the self-host or Cloud
base URL. The SDK does not care which side of the hostname is running.

## What a RAG trace records

Retrieval records hit count, rank, score, `doc_id`, ticker, section,
mode, collection, and duration. It does not record chunk text. Filing
text is public, but it is large and it is not what a reviewer needs to
see first. Scores and ids are enough to join a hit back to the index.

A generation records the model name, duration, and token counts when the
provider returned `usage`. Cost is computed only from those counts and a
known unit price. If usage is missing, cost is omitted, not invented.
The fake model still opens a generation span with `model=fake` so the
tree shape does not depend on a paid key.

A tool span records the tool name, ok, and duration. Arguments that
could hold secrets are not copied. The HTTP `traces` field on `/ask` is
a different object. It is the agent's plan log in the response body.

A gate span is a helper for issue #9. This change does not invent
allow/deny decisions. `ToolRegistry.execute` stays the wrap point #9
named. `current_trace_id()` is the hook issue #10 needs for the audit
row.

Eval tags the evaluator root with `git_sha` (already `git rev-parse
HEAD` in the results JSON) and each arm's `config_hash` (already the
IndexConfig digest). Those two values are how a later regression
compares traces across commits.

## Sampling and PII

`LANGFUSE_SAMPLE_RATE` is client-side and trace-level, 0.0 to 1.0.
Default 1.0 when tracing is on. A dropped root drops every child. Rate 0
exports nothing.

Attribute keys are an allowlist. Headers, API keys, and `Bearer` tokens
cannot be set. Question text is masked (email and long digit runs) and
truncated. Full chat messages stay off the wire. A second mask on the
Langfuse client is defense in depth. That function catches its own errors, because Langfuse drops the export
batch when mask raises.

Local compose keys in `.env.example` are localhost secrets. They are not
production credentials. Do not commit a Cloud secret key.

## What this leaves out

Live Langfuse UI screenshots need the compose stack or Cloud. A cloud
agent VM may have neither. In that case live ingestion is UNVERIFIED and
the in-memory (or file) exporter is the proof. Issue #9 still owns
policy. Issue #10 still owns the hash chain. This change only makes
those records able to point at a trace id.
