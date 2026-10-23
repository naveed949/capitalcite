# Structured answers

`POST /ask` retrieves chunks the same way `POST /search` does, then returns
either a grounded answer or a refusal. Citations copy `doc_id`, `start`,
`end`, `section`, and `source_url` from a retrieved chunk, plus `quote_start`
and `quote_end` for the quote. The quote has to be a verbatim substring of
that chunk. `ASK_MODEL=fake` quotes the first hit and does not call OpenAI.
Verification uses the same embedded Qdrant index as search.

## Sub-features

- `ask-fake-answered` posts the `cc-h-013` question with `ticker` JPM and `model` `fake`. HTTP 200, `kind` `answered`, and each citation's `doc_id`, `start`, and `end` match a hit from the same search. The quote is inside that hit's text.
- `ask-fake-unanswerable` posts `cc-u-001` with `ticker` WFC and `model` `fake`. HTTP 200, `kind` `refused`, no citations. The year 2026 is not a period year on the retrieved WFC FY2025 hits.
- `ask-openai` posts `cc-h-013` with `model` `openai`. A missing `OPENAI_API_KEY` returns HTTP 503 and is recorded as UNVERIFIED live generation. A set key that returns HTTP 200 must match the schema, and any citations must ground.

## How to get to it (user POV)

- Index the corpus, then start uvicorn with the same `QDRANT_PATH`. Embedded Qdrant locks the folder, so uvicorn cannot stay up during `capitalcite index`. `scripts/verify.sh` sets `ASK_MODEL=fake` for that process.
- POST JSON to `http://127.0.0.1:${PORT}/ask`.
- Set `model` to `fake` or `openai` on the request. Omit it to use `ASK_MODEL`.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200.
- The `POST /search` step for this run has already indexed JPM FY2024, WFC FY2025, and BAC FY2024 on the shared `QDRANT_PATH`. If that step was UNVERIFIED, `POST /ask` is UNVERIFIED. Do not mock Qdrant or the model.

- **Ask with the fake model.** POST the `cc-h-013` question (`k` 10, `ticker` `JPM`, `model` `fake`) to `http://127.0.0.1:${PORT}/ask`. Retrieval is hybrid. HTTP 200, `kind` `answered`. Each citation `doc_id`, `start`, `end`, `section`, and `source_url` matches a hit from the same query on `POST /search`, and `quote` occurs in that hit's `text`. `config_hash`, `collection_name`, and `point_count` match that search response.
- **Ask an unanswerable question.** POST the `cc-u-001` question (`k` 10, `ticker` `WFC`, `model` `fake`). HTTP 200, `kind` `refused`, empty citations. The fake model is not a judgment that the question was answerable; the year in the question is not a hit period year.
- **Ask with OpenAI.** POST the `cc-h-013` question with `model` `openai`. If `OPENAI_API_KEY` is empty, HTTP 503 is required and the step is UNVERIFIED, not a pass and not a product failure. If the key is set, HTTP 200 must validate and any citations must ground. Any other status is UNVERIFIED.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `ask.body.json`, `ask.cc-h-013.body.json`, `ask.cc-u-001.body.json`, and either `ask.openai.unverified` or a grounded `ask.openai.body.json`. A missing corpus writes `ask.unreachable`.
- **Proof.** Keep the curl status and the response bodies. A 200 from `/health` is not ask proof. Pytest is not this feature.

## Gotchas

- `ASK_MODEL=fake` does not read `OPENAI_API_KEY`. `model` `openai` without a key is HTTP 503.
- `scripts/verify.sh` forces `ASK_MODEL=fake` on the uvicorn it starts. The OpenAI check sends `model` in the JSON body.
- `cc-u-001` must be `refused`. A fake extractive answer on WFC FY2025 chunks is a failed proof.
- An empty `point_count` on `cc-h-013` is a failed proof. If ingest could not run, record `ask.unreachable`. Do not report that skip as passed.
- Do not invent answer-quality numbers from this step. Faithfulness, relevancy, and citation accuracy stay TBD until a measured eval writes them.
- `TestClient` in pytest is not this feature.
- The response field is `retrieval_score`, not `confidence`.
