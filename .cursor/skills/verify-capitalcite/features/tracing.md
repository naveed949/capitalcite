# Langfuse and OTLP tracing

`POST /ask` and `capitalcite eval` emit a trace tree when tracing is
configured. Spans cover retrieval, each model call, each tool call, gate
decisions, and, on the agent path, the graph nodes `graph.plan`,
`graph.act`, `graph.synthesize`, and `graph.stop`. Cost and latency sit
on those spans. If Langfuse keys are missing and
`OTEL_EXPORTER_OTLP_ENDPOINT` is unset, tracing is a no-op and the
request still returns.

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
- `gate-spans` runs `capitalcite gate decide` for an unknown tool, invalid
  args, and a broken policy. Each call appends a `gate` span with
  `capitalcite.tool`, `capitalcite.gate.decision` `deny`, and the reason.
- `langfuse-live` is UNVERIFIED when nothing is listening on
  `localhost:3000`. Do not start compose for this check.
- `otlp-in-process` runs when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset.
  `scripts/verify.sh` starts a local HTTP receiver, exports one `ask`
  span, and checks the protobuf body. It does not start compose.
- `otlp-configured` runs when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
  A curl of that endpoint is recorded. A dead collector is UNVERIFIED.
  Do not start the `otel` compose profile for this check.
- `gen-ai-attrs` are `gen_ai.request.model`, `gen_ai.operation.name`,
  `gen_ai.provider.name`, and `gen_ai.tool.name` on the spans that have
  them. Model spans use operation `chat`. Tool spans use `execute_tool`.

## How to get to it (user POV)

- Copy `.env.example` to `.env` and run `docker compose up -d` for the
  Langfuse UI at `http://localhost:3000`.
- Or set `CAPITALCITE_TRACE_FILE` to write spans without a UI.
- Or set `OTEL_EXPORTER_OTLP_ENDPOINT` to an HTTP collector, for example
  `http://127.0.0.1:4318` after `docker compose --profile otel up -d otel-collector`.
  Leave it unset for a no-op. `http/protobuf` and `http` are the supported
  protocols. The timeout applies to the export worker.
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
- **Gate decisions.** After the three `capitalcite gate decide` denials,
  the trace file has `gate` records whose attributes include the tool,
  `deny`, and `unknown_tool` / `invalid_args` / `policy_load_failed`.
- **Live Langfuse.** If `curl -sf http://127.0.0.1:3000` fails, write
  `langfuse.skipped` and report UNVERIFIED. Do not start compose.
- **OTLP.** If `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, the in-process
  receiver proof must see an `ask` span. If it is set, record the curl
  and leave a live collector UNVERIFIED when nothing accepts the post.
  Do not start the `otel` profile.
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
- OTLP does not require Langfuse keys. An empty
  `OTEL_EXPORTER_OTLP_ENDPOINT` installs no OTLP exporter.
- A slow collector must not change the `/ask` body. Export is queued.
- Do not treat an in-memory pytest tree as a Langfuse UI screenshot.
- `Gate.decide` writes one gate span per allow or deny. `capitalcite
  gate bench` does not open a tracer, so the p95 ceiling stays a
  decision-cost measurement.
