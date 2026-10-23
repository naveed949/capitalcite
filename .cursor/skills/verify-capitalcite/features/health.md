# Health check

Health check tells a user (or a load balancer) that the capitalcite API process is up. It returns JSON with `status`, `service`, the installed package `version`, `git_sha`, and `point_count`. `point_count` is 0 when the index is missing or cannot be read. The route stays HTTP 200. It does not check Langfuse.

## Sub-features

- `health-ok` returns HTTP 200 from `GET /health` on a live uvicorn.
- `health-json` body is `status=ok`, `service=capitalcite`, a non-empty `version`, a non-empty `git_sha`, and `point_count` as an integer greater than or equal to 0.
- `health-isolated` succeeds with Docker Compose down. A missing index still returns 200 and `point_count` 0.

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
- **Read identity.** The body has `"status":"ok"` and `"service":"capitalcite"`. `version` is present and non-empty. `git_sha` is a non-empty string. `point_count` is an integer and is at least 0.
- **Confirm process-local.** This step must pass even when nothing listens on `127.0.0.1:6333` or `127.0.0.1:3000`. Before an index exists, `point_count` is 0 and the status is still 200.
- **Bundled helper.** Run `./scripts/verify.sh`. The same request is recorded under `artifacts/verify/<timestamp>/health.body.json` and `health.exit` is `0`.
- **Proof.** Keep `health.body.json`, `health.headers`, and `health.exit`. After teardown, those files still exist and still show `status=ok`.

## Gotchas

- Settings load at process start. A `SEC_USER_AGENT` without `@` means uvicorn never becomes ready; curl will hang or get connection refused. That is a launch failure, not a health-handler bug.
- `/health` is not a readiness check. A 200 does not mean Qdrant, MinIO, or Langfuse are up. `point_count` 0 means the count could not be read. Prove Qdrant on [local-stack](./local-stack.md).
- `TestClient(app).get("/health")` in pytest is not this feature. Proof requires a live uvicorn.
- `make health` assumes port 8000. Verification uses a free port so it does not collide with a developer's server.
- Default uvicorn bind is `127.0.0.1` in the helper. Curling `0.0.0.0` is wrong.
