# Local Chat Completions

`POST /ask` with `model` `openai` can call a Chat Completions server on
loopback. The process sets `ASK_OPENAI_BASE_URL` to a `/v1` prefix and
`ASK_OPENAI_MODEL` to the model id. A loopback call does not send
`Authorization` and does not need `OPENAI_API_KEY`. The artifact for this
step says `local`. It is not a hosted OpenAI run. Real Ollama is
UNVERIFIED.

## Sub-features

- `ask-local-stub` posts a question that already has hits, with `model` `openai`, at a loopback stub that answers Chat Completions `json_object`. HTTP 200. The stub recorded `POST /v1/chat/completions` for model `lfm3:latest` with no `Authorization` header.
- `ask-local-closed` posts the same body at a closed loopback port. HTTP 503. The detail names host class `localhost` and does not include the URL. The curl limit is 10 seconds.
- `ollama-unverified` is the live `lfm3:latest` command in the README. This helper does not start Ollama and does not run the 95-item bench.

## How to get to it (user POV)

- The API is already up on the indexed store from the structured-answers step.
- A small HTTP stub listens on `127.0.0.1` and prints its port.
- uvicorn restarts with `ASK_MODEL=openai`, `ASK_OPENAI_BASE_URL=http://127.0.0.1:${STUB}/v1`, and `ASK_OPENAI_MODEL=lfm3:latest`, and with `OPENAI_API_KEY` unset.
- After the closed-port check, uvicorn restarts with `ASK_MODEL=fake` and without `ASK_OPENAI_BASE_URL`.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `GET /health` is 200 and the index already answered a fake `POST /ask`, so the question has hits.
- The default verify launch stays `ASK_MODEL=fake` until this step restarts it.

- **Ask the stub.** POST the same question the fake step answered, with `model` `openai`, to `http://127.0.0.1:${PORT}/ask`. HTTP 200. The stub log has path `/v1/chat/completions`, model `lfm3:latest`, and no `Authorization` header. Write `ask.local.label` as `local` and `ask.local.note` that this is a stub, not hosted OpenAI.
- **Ask a closed port.** Restart uvicorn with `ASK_OPENAI_BASE_URL` on a port that is not listening. POST the same body with `curl --max-time 10`. HTTP 503. The body names host class `localhost` and does not contain `://` or the port. A hang past 10 seconds is a failure.
- **Restore fake.** Restart uvicorn with `ASK_MODEL=fake` and an empty `ASK_OPENAI_BASE_URL` before later steps.
- **Proof.** Keep the stub log, both HTTP statuses, and `ask.local.label`. Pytest is not this feature. Do not record this step as hosted OpenAI.

## Gotchas

- The main verify launch stays `ASK_MODEL=fake`. This step is a separate restart.
- A body `"model": "openai"` uses the process base URL. It does not make the call hosted.
- Embeddings still use `https://api.openai.com/v1/embeddings` when `EMBEDDER=openai-small`. This step does not change `EMBEDDER`.
- Do not run `eval --suite bench` against the stub. The fake bench already runs. Local labels are covered by pytest.
- Live Ollama at `http://localhost:11434/v1` with `lfm3:latest` is UNVERIFIED.
- Hosted OpenAI without `OPENAI_API_KEY` stays the separate `ask.openai` probe and is UNVERIFIED.
