# Health check

Health check tells a user (or a load balancer) that the capitalcite API process is up. It returns JSON with `status`, `service`, and the installed package `version`. It does not check Qdrant or Langfuse.

## Sub-features

- `health-ok` returns HTTP 200 from `GET /health` on a live uvicorn.
- `health-json` body is `status=ok`, `service=capitalcite`, and a non-empty `version`.
- `health-isolated` succeeds with Docker Compose down (process-local liveness).

## How to get to it (user POV)

- Run `uv run uvicorn capitalcite.api:app` (or `./scripts/verify.sh`) and request `GET /health`.
- From the Makefile, after a server is already on 8000: `make health` (`curl -sf http://127.0.0.1:8000/health`).
- README quickstart: `curl -s localhost:8000/health`.

## Driving it with curl

Preconditions:

- A uvicorn this run started is listening on `http://127.0.0.1:${PORT}`.
- `SEC_USER_AGENT` for that process contains an `@`.
- Doctor on that URL has not failed since launch.

- **Request health.** Run `curl -sS -D artifacts/verify/$TS/health.headers -o artifacts/verify/$TS/health.body.json http://127.0.0.1:${PORT}/health`. HTTP status is 200. Body is JSON.
- **Read identity.** The body has `"status":"ok"` and `"service":"capitalcite"`. `version` is present and non-empty.
- **Confirm process-local.** This step must pass even when nothing listens on `127.0.0.1:6333` or `127.0.0.1:3000`.
- **Bundled helper.** Run `./scripts/verify.sh`. The same request is recorded under `artifacts/verify/<timestamp>/health.body.json` and `health.exit` is `0`.
- **Proof.** Keep `health.body.json`, `health.headers`, and `health.exit`. After teardown, those files still exist and still show `status=ok`.

## Gotchas

- Settings load at process start. A `SEC_USER_AGENT` without `@` means uvicorn never becomes ready; curl will hang or get connection refused. That is a launch failure, not a health-handler bug.
- `/health` is not `/ready`. A 200 does not mean Qdrant, MinIO, or Langfuse are up. Prove those on [local-stack](./local-stack.md).
- `TestClient(app).get("/health")` in pytest is not this feature. Proof requires a live uvicorn.
- `make health` assumes port 8000. Verification uses a free port so it does not collide with a developer's server.
- Default uvicorn bind is `127.0.0.1` in the helper. Curling `0.0.0.0` is wrong.
