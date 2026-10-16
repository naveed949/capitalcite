# Langfuse tracing

`POST /ask` and `capitalcite eval` emit a trace tree when tracing is
configured. Spans cover retrieval, each model call, each tool call, and
(when a gate exists) gate decisions. Cost and latency sit on those
spans. If Langfuse keys are missing, tracing is a no-op and the request
still returns.

## Sub-features

- `ask-span-tree` posts `cc-h-013` with `model` `fake` while
  `CAPITALCITE_TRACE_FILE` points at a JSONL file. After HTTP 200 the
  file contains a root `ask` span and a child `retrieve` span. Generation
  is present when the fake model ran.
- `ask-unconfigured` posts the same question with tracing disabled. HTTP
  200 is unchanged. The trace file is absent or empty.
- `eval-tags` runs `capitalcite eval --suite golden --small` with a trace
  file. The eval root records `git_sha`. `config_hash` is on that root when
  every arm shares one hash, and on each `eval-arm` span otherwise.
- `langfuse-live` is UNVERIFIED when nothing is listening on
  `localhost:3000`. Do not start compose for this check.

## How to get to it (user POV)

- Copy `.env.example` to `.env` and run `docker compose up -d` for the
  Langfuse UI at `http://localhost:3000`.
- Or set `CAPITALCITE_TRACE_FILE` to write spans without a UI.
- Leave keys empty, or set `LANGFUSE_TRACING_ENABLED=false`, for a no-op.

## Driving it with curl and the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200.
- For `ask-span-tree`, the `POST /ask` fake step has an index, and
  uvicorn was started with `CAPITALCITE_TRACE_FILE` set to a path this
  run owns. `scripts/verify.sh` does that.

- **Ask with a file exporter.** After the fake `cc-h-013` POST, the trace
  file exists and is JSONL. At least one record has name `ask` and one
  has name `retrieve`. The retrieve record has `capitalcite.hit_count`.
  No record value contains `sk-` or `Bearer `.
- **Eval tags.** `capitalcite eval --suite golden --small` with the same
  file env writes an `eval` span that includes `capitalcite.git_sha`.
  `capitalcite.config_hash` is on that span when the arms share one hash,
  and on the `eval-arm` spans when they do not.
- **Live Langfuse.** If `curl -sf http://127.0.0.1:3000` fails, write
  `langfuse.skipped` and report UNVERIFIED. Do not start compose.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `spans.jsonl`
  and either `langfuse.skipped` or a note that the UI was reachable.
- **Proof.** Keep the JSONL and the skip file. Pytest is not this
  feature. A 200 from `/health` is not tracing proof.

## Gotchas

- `/health` does not talk to Langfuse. A healthy API with compose down is
  expected.
- `conftest.py` forces `LANGFUSE_TRACING_ENABLED=false`. Pytest never
  dials `:3000`.
- File export does not require Langfuse keys. Live ingestion does.
- Do not treat an in-memory pytest tree as a Langfuse UI screenshot.
- Gate spans appear when a caller uses `gate_decision`. The fail-closed
  gate in issue #9 is the production caller.
