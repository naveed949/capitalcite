# 0016. README as the measured project page

Date: 2026-10-17

Status: Accepted

Issue #14 asks for a README a reader can use as the front
page: real eval numbers, an architecture diagram, a short demo, a
tradeoffs section, and a fresh-clone quickstart that works in under 15
minutes. This record is why the page leads with the fake-model path,
why every table cell points at JSON produced in the same run, and why
UNVERIFIED is a legal cell.

## The README is the product surface

The code already has ingest, hybrid search, grounded `/ask`, a
LangGraph agent, a fail-closed gate, an audit chain, MCP, an optional
graph tool, and `capitalcite check`. The gap is not another endpoint.
It is that the README still read as a scaffold: docker compose first,
a mermaid that named PydanticAI, rerank, and RAGAS as if they sat on
the serve path, and TBD cells next to copied older-SHA numbers.

A reader will not open fifteen ADRs. They will open README.md. The
page therefore has to be the one URL that shows the pipeline, the
measured numbers, a replayable terminal demo, and the design arguments
that decide how the serve path works.

## One local path. Docker is a fork.

`GET /health`, `capitalcite chunk` on
`tests/fixtures/chunking/corpus`, `capitalcite index` with
`QDRANT_PATH`, and `POST /ask` with `ASK_MODEL=fake` do not need
Compose, an OpenAI key, or live EDGAR. Path-mode Qdrant wins over
`QDRANT_URL` (`retrieval/service.py`). `:memory:` cannot be shared
with uvicorn, so the documented store is a directory.

Leading with `docker compose up -d` made the common case the one that
fails on a VM without Docker. The fake-model quickstart is the default.
Compose Qdrant, Langfuse `:3000`, live EDGAR ingest of twelve 10-Ks,
`ASK_MODEL=openai`, Neo4j Bolt, and Azure apply stay optional and
UNVERIFIED when this run cannot execute them.

The 15-minute clock is that default path, timed from a fresh clone in
this change at 11 seconds on a 4-core VM with warm `uv` and Hugging
Face caches (`docs/demo/quickstart-timing.log`). The recording under
`docs/demo/` is the same commands.

## Numbers come from JSON this run wrote

ADR 0006 already said README retrieval cells are generated from
`evals/results/<sha12>.json`, not typed by hand. `make eval-report`
and `capitalcite eval --readme` stay the lever for the six-arm table
and the agent-vs-RAG block.

This change extends the same rule to every remaining TBD. Gate, audit,
`capitalcite check`, and the UNVERIFIED list are committed JSON next
to the eval files. A cell that this run could not produce (live
OpenAI, RAGAS, minilm, hybrid+rerank, Langfuse UI, Azure `/health`,
Compose) is the string UNVERIFIED plus a reason in
`evals/results/<sha12>-unverified.json`. Copying `8b63d12acd50.json`
into a new README would be a different commit's measurement. Tests
still pin that file as the nightly snapshot source. The published
README table points at this run's SHA.

`--small` Recall@5 on the fixture HTML is not the twelve-filing table.
Those excerpts do not contain the gold spans. Publishing 0.000 as if
it were corpus recall would be a lie about the limiter.

## The diagram matches the serve path

The mermaid block is a map of modules that exist:
ingest → chunk (section or fixed) → embed (bge-small dense + BM25) →
Qdrant RRF → `/search` and `/ask` RAG → LangGraph agent through
`Gate.execute` and the hash-chained audit log → stdio MCP → optional
`graph_query` → Langfuse or `CAPITALCITE_TRACE_FILE` → `capitalcite
check` / nightly eval → Azure Bicep without a live apply.

It does not draw a cross-encoder, PydanticAI, or RAGAS on that path.
Those are either UNVERIFIED or explicitly out of the serve path (ADR
0005, 0008, 0006).

## Tradeoffs live on the README because that is the first page

ADRs 0003, 0005, 0008, 0009, 0011, and 0014 already hold the full
arguments. The README section restates the ones a reader will ask:
why section vs fixed, why RRF and no reranker yet, why default-deny
plus extra-key denial plus a hash chain, why the agent reuses `ask()` /
`ground()`, and why the graph is a tool rather than a retrieve() mode.
Each claim that quotes a rate points at the JSON from this run.

## What this does not change

No new Python API. No paid provider. No Azure apply. `8b63d12acd50.json`
stays in the tree because `tests/test_check.py` compares the nightly
baseline to that published matrix. Recursive chunking stays in the
CLI and out of the default six-arm table.
