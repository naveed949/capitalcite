# 0028. OTLP beside the file and Langfuse exporters

Date: 2026-11-03

Status: Accepted

An operator looking at one `/ask` wants the time spent in each graph node, each tool call, and each model call. The answer a banker reads must stay the same. Tracing stays off until `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

## A trace is one request

`POST /ask` and `POST /ask/stream` both enter `iter_ask`. That function opens the root `ask` span. A child span reads the current parent from a context variable. `asyncio.create_task` copies that context into the agent task, so the `agent` span stays under `ask` even though the graph runs on another task. The SSE loop stays on the request task. The `ask` span stays open across each yield. A disconnect watcher is a different task and must not become a parent.

Span ids are 16 hex characters. Trace ids are 32. The file exporter and the audit log already store those ids. `OtlpExporter` copies the same integers onto spans from a private `TracerProvider`. It does not call `set_tracer_provider`. Langfuse 5 mints its own OTel ids. A process-wide provider would attach our spans to those ids, or the other way around.

Graph nodes open `graph.plan`, `graph.act`, `graph.synthesize`, and `graph.stop`. Model spans are named `chat {model}`, matching the OpenTelemetry GenAI semantic convention. The parent distinguishes a planner call (`graph.plan`) from a synthesizer call (`graph.synthesize`). Tool, gate, and MCP spans keep their names. The parent of a tool span is the node that called the tool.

## gen_ai attributes name the model and the tool

Collectors already chart `gen_ai.request.model`, `gen_ai.operation.name`, `gen_ai.provider.name`, and `gen_ai.tool.name`. We already stored the model name and token counts. The new keys sit on the same allowlist. Unknown keys are still dropped. The question text is still masked.

A generation uses operation `chat`. A tool span uses operation `execute_tool` and `gen_ai.tool.name`. The provider is `fake` for the deterministic model and `openai` for the chat completion client. A gate span carries `gen_ai.tool.name` for the tool it judged. It does not claim to have run the tool.

## Head sampling stays at the root

`LANGFUSE_SAMPLE_RATE` decides at the root, before any exporter runs. A dropped root drops every child. That is head sampling. Tail sampling would keep every span until the request finished, then drop the fast ones and keep the slow ones. We do not do that. The file exporter and OTLP must record the same tree. A second sample inside the OTLP exporter would split them.

## The post leaves the request path

`FileExporter.export` appends one JSONL line while the request is still on the stack. An HTTP post in that same function would add collector latency to every `/ask`. `BatchSpanProcessor` appends the finished span to a queue. A worker thread sends HTTP protobuf. `OTEL_EXPORTER_OTLP_TIMEOUT` defaults to 0.2 seconds and applies to that worker. A collector that is down, unreachable, or slow is logged. The handler still returns the answer it would have returned with tracing off.

Measured on this 4-core machine, one in-memory chunk, `ASK_MODEL=fake`, 40 timed `POST /ask` calls per side. Off was 2.2 ms p50 and 2.9 ms p95. On was 2.6 ms p50 and 3.5 ms p95. The difference is 0.4 ms at p50 and 0.6 ms at p95. Per-round p50 differences were 0.16 ms and 0.54 ms. Tracing was slower in both rounds. The timer does not include the collector round trip. The file is `evals/results/701e39b7489a-otel-overhead.json`.

## HTTP protobuf, not gRPC

`http/protobuf` and `http` construct `OTLPSpanExporter`. Any other protocol, including `grpc`, logs a warning and skips OTLP. `grpcio` is not a dependency. `fastapi[standard]` had already locked the HTTP exporter, so declaring it does not add a wheel. The setting is a base URL such as `http://127.0.0.1:4318`. The exporter appends `/v1/traces` when the path is missing.

The image was not rebuilt here. Docker is absent on this machine, so image size stays UNVERIFIED. No new package version entered `uv.lock`.

## What we did not pick

Langfuse alone shows the tree when keys are set. It does not speak OTLP to a collector the operator already runs, and its span ids are not ours.

LangSmith is another hosted product and another client on the request path.

A log line can name a node. It does not record a parent, a duration, or a queryable trace.

Replacing `observation()` with the OpenTelemetry SDK would rewrite the hot path and collide with Langfuse.

Keeping span names `plan`, `complete`, and `generate` would have preserved our internal step names. Collectors that already group GenAI spans by `chat {model}` would then need a rewrite. Attributes already record the model. The name now matches those attributes.

## Decision

`observation()` stays the only caller API. `OtlpExporter` is installed only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. An empty endpoint, a non-HTTP protocol, or a constructor failure leaves that exporter out. The file exporter and the Langfuse exporter stay. An invalid observation kind is dropped in `_RecordingTracer.start`, so exporters never see it. A Langfuse `__enter__` failure still `__exit__`s the manager so `_live` stays empty. Answers do not read span records. Tracing on versus off is checked on rag `ask()`, streamed rag, and agent. The non-refusal subset includes answered items.
