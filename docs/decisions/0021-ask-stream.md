# 0021. Streaming answers over SSE

Date: 2026-10-23

Status: Accepted

`POST /ask` waits for the whole run, then returns one JSON object. A
caller that wants the plan, each gated tool, and the citations as they
happen needs a stream. This record is why that stream is
`POST /ask/stream` with `text/event-stream`, why the frames reuse the
same Pydantic models as `/ask`, and why the graph still runs
plan → act* → synthesize.

## POST, not GET

The body is `AskRequest`. Filters, `path`, and `model` already live
there. GET would copy those fields onto the query string or invent a
second request type. `EventSource` in the browser is GET-only. This
API has no browser client. curl, httpx, and any caller that can POST
JSON can read SSE. The route is POST. There is no GET twin.

## SSE, not a WebSocket, not long-poll

The server writes facts. The client does not send mid-run messages.
A WebSocket would add a handshake, a frame type, and a close code for
a one-way job. Long-poll would hide each fact behind another request
and would still need an event schema.

SSE is one HTTP response, `Content-Type: text/event-stream`, and a
sequence of `event:` / `data:` blocks. The client parses lines. The
server can end the stream with `done`. Starlette's `StreamingResponse`
is that response. The project does not add `sse-starlette`.

Starlette cancels the generator when the client drops. The generator's
`finally` closes the event iterator. The agent run is one child task
plus a queue. That `finally` cancels the task. `CancelledError` is a
`BaseException`. It is not a gate deny and it is not an `error` event.
A second planned tool does not start after cancel.

Backpressure is the queue and the `yield`. `Observe` methods only
`put_nowait` in-memory events. They do not write sockets. If the
client reads slowly, the generator pauses at `yield` and the queue
holds mid-run facts until the next read. The process does not buffer
the whole run as one blob.

## Events are not graph transitions

LangGraph still owns plan → act* → synthesize. `ainvoke` is unchanged.
The stream does not subscribe to LangGraph's internal `astream` and
does not rename node names as HTTP events.

A side channel, `Observe`, sees the pending plan, the pending step
before `execute`, and the one `TraceStep` the act node already built,
plus `ToolResult.reason`. The route turns those into `plan`,
`tool_call`, `tool_result`, or `tool_denied`. RAG has no plan. It
emits `citation` and then `answered` or `refused`. Fake generation is
one shot, so there is one `answer_delta`.

`/ask` folds the same iterator and returns the terminal `AskResponse`.
The two routes share `iter_ask`. A new field on the HTTP object would
have to appear on both. The stream does not grow a second answer type.

## Reuse the models

`PlanStep`, `TraceStep`, `Citation`, and `AskResponse` already have
validators. The event union wraps those objects. OpenAPI lists the
event names from that union. A client that already validates `/ask`
can validate `answered.response` with the same model.

`tool_denied` is an audited deny (`unknown_tool`, `invalid_args`, and
the other `DenyReason` values except `gate_error`). The act node still
stores one `TraceStep`. The wrapper reads `reason` before that node
drops it. The audit log still writes decide → record → run. A deny
still produces a JSONL row.

`AskRequest` and `AskResponse` do not gain stream-only fields.

## What this leaves out

GET `EventSource`, a WebSocket, token-by-token OpenAI deltas, and a
second router module stay out. Live `model=openai` without a key is
still HTTP 503, as on `/ask`. The fixture corpus cannot prove a
two-bank comparison; that proof stays on the ingested twelve 10-Ks.
`agent/fdic.py` and CLI metrics are unchanged.
