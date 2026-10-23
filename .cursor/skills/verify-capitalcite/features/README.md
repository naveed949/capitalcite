# capitalcite verification map

This directory is the maintained source for verifying the user-facing behavior of capitalcite. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Work from the repository root.
- Run `uv sync --frozen` (or `./scripts/verify.sh`, which does that).
- Launch uvicorn on `127.0.0.1` with a free port and `SEC_USER_AGENT` that contains an email (the helper uses `capitalcite-verify you@example.com`).
- Doctor: `GET /health` is 200 with `status=ok` and `service=capitalcite`.
- Never drive an instance this run did not start.
- Docker Compose is optional. Do not require it for `/health` or `capitalcite version`.
- Put proof in `artifacts/verify/<timestamp>/`. Do not commit it.

## Driving conventions

- Start every recipe from the baseline state unless its preconditions say otherwise.
- Treat every command as literal. Keep quoted names, paths, and flags unchanged.
- HTTP: `curl` against `http://127.0.0.1:${PORT}`.
- CLI: `uv run capitalcite ...` so the editable install is used.
- Prefer `./scripts/verify.sh` when the recipe is one of the bundled steps; use the raw curl/CLI when extending.
- Restore nothing: health and version do not mutate store state. Ingest isolates writes under `artifacts/verify/<timestamp>/data` via `--output-dir` so a developer's `data/` is untouched. Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final body.
- HTTP proof includes status code and response body.
- CLI proof includes the command, stdout, stderr, and exit code.
- Optional compose or Qdrant steps that cannot run (no Docker, nothing on 6333) are `UNVERIFIED`. Record the skip reason. Never report them as passed.
- Azure deploy, live EDGAR 403, and any other path that needs credentials or a network you do not have is `UNVERIFIED`, never passed.
- Record the feature ID with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition, status `UNVERIFIED`.
- Do not report a skipped entry point as verified through a different path (pytest is not a substitute for curl).

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with <harness>` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

Keep implementation details out of the map. Name only user paths, stable handles, required state, commands, and observable proof.

## Features

- [Health check](./health.md) covers `GET /health` on a live uvicorn process, including `git_sha` and `point_count`.
- [CORS](./cors.md) covers `CORS_ORIGINS` on the live uvicorn.
- [Ask rate limit](./rate-limit.md) covers `ASK_RATE_LIMIT` on `POST /ask` and `POST /ask/stream`.
- [Demo image](./image.md) covers `make image`, CLI help, and `GET /health` inside the image.
- [CLI version](./cli-version.md) covers `capitalcite version` and the `--help` entry point.
- [EDGAR ingest](./ingest.md) covers `capitalcite ingest --tickers … --forms … --years …`.
- [Parse and chunk](./chunk.md) covers `capitalcite chunk --chunker section` on the checked-in fixture tree.
- [Golden Q&A set](./golden.md) covers `capitalcite golden validate`.
- [Hybrid search](./search.md) covers `POST /search`, `capitalcite index --help`, and the quote-index recall sanity.
- [Retrieval eval](./eval.md) covers `capitalcite eval --suite golden` and the `--small` CI path.
- [Structured answers](./ask.md) covers `POST /ask`.
- [Streaming answers](./stream.md) covers `POST /ask/stream`.
- [Plan-then-act agent](./agent.md) covers `POST /ask` with `path` `agent`.
- [Fail-closed tool gate](./gate.md) covers `capitalcite gate validate`, `decide`, `redteam`, and `bench`.
- [Langfuse tracing](./tracing.md) covers `CAPITALCITE_TRACE_FILE` span JSONL after `POST /ask` and `capitalcite eval --small`.
- [Audit log](./audit.md) covers `capitalcite audit verify`.
- [Behavioral regression check](./check.md) covers `capitalcite check` against `evals/baselines/ci.json`.
- [MCP server](./mcp.md) covers `capitalcite mcp` over stdio with the official MCP client.
- [Local compose stack](./local-stack.md) covers `docker compose config` and Qdrant `/healthz` when Docker is present.
- [Optional entity graph](./graph.md) covers `capitalcite graph` and `eval --suite graph --small`.
- [Metrics extract](./metrics-extract.md) covers `capitalcite metrics extract --fixtures` and the BankFind host `api.fdic.gov/banks`.

## Extending the map

New endpoints and commands (`capitalcite audit verify`) each get their own feature file in this directory, a row in the list above, and a step in `scripts/verify.sh`. See the parent skill's **Extending this skill** section.
