# 0001. Project scaffold

Date: 2026-10-15
Status: Accepted

This is the first decision record. It explains the layout and tools that later
issues (#2 ingest, #3 chunking, #4 hybrid retrieval, #5 golden set, #6 evals)
are meant to land on. Nothing here is RAG yet — only the rails.

## uv, not pip + venv + poetry

`uv` is one binary that creates the venv, resolves dependencies, writes a
lockfile, and runs tools (`uv run ruff`, `uv run pytest`). The lockfile is
what CI installs (`uv sync --frozen`), so a PR cannot silently pick up a
newer FastAPI than the author tested.

Poetry would also lock, but `uv` is faster and is what the milestones already
named. pip-tools plus a hand-rolled venv works; it is more moving parts for
the same outcome. The cost of `uv` is that it is younger than pip. Astral
backs it, and the workflow (`uv sync`, `uv run`) is the one the README
promises.

Python is pinned to 3.12 because that is the issue's contract and because we
want one interpreter in CI, not a matrix, until there is a reason to care
about 3.13.

## Async FastAPI

The first real I/O in this repo is EDGAR (rate-limited HTTP), then Qdrant,
then LLM calls. Those waits should not occupy a worker thread. FastAPI on
uvicorn is an ASGI server: `async def` handlers release the event loop while
`httpx`/`qdrant-client` talk to the network.

This is not a speed cheat for CPU work. Chunking HTML and running BM25 will
still be CPU-bound; async does not help there, and we should not pretend it
does. Those steps can run in a process pool later if they show up on a
profile. Flask or sync Django would be simpler to read, and wrong for an API
that spends most of its time waiting on EDGAR and a vector store.

The app is built with a factory (`create_app`) and a lifespan hook so later
issues can open a Qdrant client at start and close it at shutdown without
rewriting the module.

## Settings

One Pydantic v2 `Settings` class, loaded from the environment and optional
`.env`. Validation happens when the process starts. If `SEC_USER_AGENT` is
missing or has no `@`, uvicorn does not boot. That is deliberate: EDGAR's
fair-access rule is a hard constraint, not an ingest-time afterthought.

`.env` is shared with docker compose. Compose needs dozens of keys the app
does not. `extra="ignore"` means those keys do not crash Settings. The
alternative — two env files — is how secrets drift.

Qdrant and Langfuse fields are declared now, even though this issue does not
open clients for them. The next issues should not invent a second config
style.

## Vector store: Qdrant, not Postgres + pgvector

Issue #1 allowed either. We picked Qdrant.

Hybrid retrieval (#4) is BM25 plus dense vectors, fused with Reciprocal Rank
Fusion. Qdrant is built for that shape: dense vectors, sparse vectors,
payload filters, and in-engine fusion. pgvector is a dense index inside
Postgres. Keyword search would then be a second system (Postgres FTS, or
BM25 in process). We would be assembling hybrid search by hand on day one.

Langfuse already needs Postgres. Putting vectors in that same database
couples RAG data to an observability product's schema and backups. Running
two Postgres instances to avoid that coupling is more ops, not less.

Citation metadata (ticker, form, year, section, source URL) maps cleanly onto
Qdrant payloads. That is the data model #3 and #4 will filter on.

The cost: another service to run, no SQL joins, and Azure later (#13) may
already have Postgres. If deploy-time cost says "one database," the retrieval
module should hide the backend so we can swap. That is a later decision, not
this one. The audit log in #10 can still be Postgres or hash-chained JSONL;
it does not have to live next to the vectors.

## Langfuse in compose

Every retrieval, LLM call, tool call, and gate decision needs a trace if the
README's "every change is measured" line is going to be true. Langfuse is
that trace store. LangSmith is the obvious alternative; it is hosted-only in
practice and weaker for a portfolio that talks about auditability and
self-hosting.

Current Langfuse is not one container. Self-host is web + worker + Postgres
+ ClickHouse + Redis + MinIO. That is heavy for a laptop. We still ship it,
because that is the real shape of the tool we will use in #11, and because
`docker compose up` in the issue means "the stack," not "a stub."

If RAM is tight: `docker compose up -d qdrant` and point `LANGFUSE_HOST` at
Langfuse Cloud. The SDK does not care which side of the hostname is running.
Compose seeds a local project (`LANGFUSE_INIT_*`) so the keys in
`.env.example` work without clicking through a UI.

We do not import the Langfuse SDK in this issue. Traces without retrieval or
generation would be empty ceremony.

## CI

One GitHub Actions job on every pull request and on `main`: ruff (lint +
format), mypy (strict), pytest. Installed from the lockfile. Python 3.12
only.

Three jobs would look more "production" and would add no signal on a
repository this small. A nightly eval gate (#12) is a separate workflow,
because it needs models, data, and a baseline file this scaffold does not
have.

CI does not run docker compose. `/health` is a process liveness check, not a
probe of Qdrant. If health required Qdrant, unit tests would need Docker, and
`uvicorn` alone would fail the issue's acceptance test when compose was
slow to boot. Integration tests against Qdrant belong with #4.

## Layout

```
src/capitalcite/     installable package (src layout so tests cannot
                     accidentally import an uninstalled tree)
  api/               FastAPI app and routes
  cli.py             `capitalcite` entry point
  config.py          settings
tests/               pytest; no live network
docs/decisions/      design records, starting with this file
evals/               empty rails for #5 / #6
data/                empty rails for #2; gitignored contents
docker-compose.yml   Qdrant + Langfuse
.github/workflows/   this CI
```

src layout plus an editable install (`uv sync`) is the boring choice that
stops `python tests/foo.py` from masking packaging mistakes.

What this issue does not do, on purpose: EDGAR ingest, chunkers, embeddings,
BM25, `/ask`, an agent, a Dockerfile (that is #13), and any eval numbers.
