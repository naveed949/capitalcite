# Streaming answers

`POST /ask/stream` takes the same JSON body as `POST /ask` and returns
`text/event-stream`. Each frame is one typed event. The last event is
`done`. An `answered` frame's `response` equals the JSON from `POST /ask`
for the same body. `ASK_MODEL=fake` quotes a retrieved chunk and does
not call OpenAI. Verification uses the same Qdrant index as `/ask`.

## Sub-features

- `stream-rag-answered` posts the same question as the fake `/ask` proof. HTTP 200, `Content-Type` starts with `text/event-stream`. Events include `citation`, `answer_delta`, `answered`, then `done`. The `answered` payload equals the `/ask` body.
- `stream-rag-refused` posts the same unanswerable question as the fake `/ask` refusal. Events are `refused` then `done`. The `refused` payload equals the `/ask` body.
- `stream-agent` posts `path` `agent` with `model` `fake` on a question the index can plan. Events start with `plan` and include `tool_call` plus `tool_result` or `tool_denied`, then a terminal `answered` or `refused`, then `done`.
- `stream-422` posts a blank `question`. HTTP 422. The response is not `text/event-stream`.

## How to get to it (user POV)

- Index the corpus, then start uvicorn with the same `QDRANT_PATH`. `scripts/verify.sh` sets `ASK_MODEL=fake` for that process.
- POST JSON to `http://127.0.0.1:${PORT}/ask/stream` with `Accept: text/event-stream` (optional) and `Content-Type: application/json`.
- Use the same body fields as `POST /ask`: `question`, optional `ticker` / `form` / `period` / `k` / `model` / `path`.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200.
- The `POST /ask` step for this run has already used the shared `QDRANT_PATH`. If that step was UNVERIFIED, `POST /ask/stream` is UNVERIFIED unless this run used `./scripts/verify.sh --small` and indexed the fixture chunks. Do not mock Qdrant or the model. Do not reuse the `/ask` JSON as stream proof.

- **Stream a fake RAG answer.** POST the same body as the answered `/ask` proof to `http://127.0.0.1:${PORT}/ask/stream`. HTTP 200. `Content-Type` contains `text/event-stream`. The body is SSE frames (`event:` / `data:`). One `answered` frame's `response` equals the `/ask` JSON. The last event is `done`.
- **Stream a refusal.** POST the same body as the refused `/ask` proof. Frames are `refused` then `done`. The `response` equals that `/ask` JSON.
- **Stream an agent run.** POST `path` `agent` and `model` `fake`. The first event is `plan` when the planner emits tool calls. Later frames include `tool_call` and either `tool_result` or `tool_denied`. The stream ends with `done`.
- **Reject a blank question.** POST `{"question":"   "}`. HTTP 422. `Content-Type` is not `text/event-stream`.
- **Bundled helper.** Run `./scripts/verify.sh --small` on the fixture index, or `./scripts/verify.sh` after a live ingest. It writes `stream.body`, `stream.http`, and `stream.events.json` (or `stream.unreachable`).
- **Proof.** Keep the curl status, the raw SSE body, and the parsed events. A 200 from `/ask` is not stream proof. Pytest is not this feature.

## Gotchas

- The method is POST. GET `/ask/stream` is 405.
- `scripts/verify.sh` forces `ASK_MODEL=fake` on the uvicorn it starts. Send `model` in the JSON body to override.
- `--small` indexes `tests/fixtures/chunking/corpus` (two JPM 10-K excerpts). It can prove RAG stream equality and a one-bank agent plan. It cannot prove `cc-u-001` year-guard refusal or a JPM-vs-BAC comparison. Those stay on the ingested corpus or are UNVERIFIED.
- A missing corpus without `--small` writes `stream.unreachable`. Do not report that skip as passed.
- `TestClient` in pytest is not this feature.
- Do not treat a folded `/ask` body as a stream. The proof is the SSE frames.
