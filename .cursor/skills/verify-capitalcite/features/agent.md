# Plan-then-act agent

`POST /ask` with `path` `agent` runs a planner, tool calls, then the same
grounded synthesizer as one-shot `/ask`. The response includes `plan` and
`traces`. `model` `fake` searches each named ticker and year and does not
call OpenAI. Verification uses the same embedded Qdrant index as search.

## Sub-features

- `agent-comparison` posts the `cc-h-055` question with `path` `agent` and `model` `fake`. HTTP 200. `plan` has at least two `search_filings` steps. `traces` is non-empty. If `kind` is `answered`, each citation quote is a substring of a retrieved chunk from those searches.
- `agent-multihop` posts the `cc-h-061` question with `path` `agent` and `model` `fake`. HTTP 200, non-empty `plan` and `traces`, `steps_used` at least 2.
- `agent-openai` posts `cc-h-055` with `path` `agent` and `model` `openai`. A missing `OPENAI_API_KEY` returns HTTP 503 and is recorded as UNVERIFIED live generation.

## How to get to it (user POV)

- Index the corpus, then start uvicorn with the same `QDRANT_PATH`. `scripts/verify.sh` sets `ASK_MODEL=fake` for that process.
- POST JSON to `http://127.0.0.1:${PORT}/ask` with `"path":"agent"`.
- Set `model` to `fake` or `openai` on the request.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200.
- The `POST /search` step for this run has already indexed JPM FY2024, WFC FY2025, and BAC FY2024 on the shared `QDRANT_PATH`. If that step was UNVERIFIED, the agent path is UNVERIFIED. Do not mock Qdrant or the model.

- **Compare two banks with the fake model.** POST the `cc-h-055` question (`path` `agent`, `k` 10, `model` `fake`) to `http://127.0.0.1:${PORT}/ask`. HTTP 200. `path` is `agent`. `plan` contains at least two `search_filings` steps. `traces` is a non-empty list. `steps_used` is at least 2.
- **Run a multi-hop item.** POST the `cc-h-061` question with `path` `agent` and `model` `fake`. HTTP 200, non-empty `plan` and `traces`.
- **Ask with OpenAI.** POST `cc-h-055` with `path` `agent` and `model` `openai`. If `OPENAI_API_KEY` is empty, HTTP 503 is required and the step is UNVERIFIED, not a pass and not a product failure.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `agent.cc-h-055.body.json`, `agent.cc-h-061.body.json`, and either `agent.openai.unverified` or a live body. A missing corpus writes `agent.unreachable`.
- **Proof.** Keep the curl status, the response bodies, and the plan/trace fields. Pytest is not this feature.

## Gotchas

- `path` defaults to `rag`. Omitting it does not exercise the agent.
- `ASK_MODEL=fake` does not read `OPENAI_API_KEY`. `model` `openai` without a key is HTTP 503.
- The fake planner searches tickers and years named in the question. A one-shot `ticker` filter on the request is not how the agent finds the second bank.
- Do not invent faithfulness or citation-accuracy numbers from the fake path. Those cells stay TBD until a live LLM eval writes them.
- `TestClient` in pytest is not this feature.
