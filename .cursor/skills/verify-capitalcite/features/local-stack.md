# Local compose stack

Docker Compose is how a user starts Qdrant (vectors) and the self-hosted Langfuse stack (web, worker, Postgres, ClickHouse, Redis, MinIO). Verification of the YAML and of a **already running** Qdrant is optional: the API health check must not depend on it.

## Sub-features

- `compose-config` validates `docker-compose.yml` with `docker compose config` when Docker exists.
- `qdrant-health` returns 200 from `GET http://127.0.0.1:6333/healthz` when Qdrant is already listening.
- `compose-absent` records a skip (not a pass, not a fail) when the Docker CLI is missing.

## How to get to it (user POV)

- Run `docker compose config` to see the rendered stack (no containers started).
- Run `docker compose up -d` or `make up` to start services; `docker compose up -d qdrant` for vectors only.
- Open Qdrant at `http://127.0.0.1:6333` (HTTP) / `127.0.0.1:6334` (gRPC). Langfuse UI is `http://localhost:3000`. MinIO S3 API is `http://127.0.0.1:9090`.

## Driving it with docker compose and curl

Preconditions:

- This recipe is optional. If `docker` or `docker compose` is missing, write `compose.skipped` with that reason and stop. Do not fail the whole verification run.
- Do not start or stop compose as part of a default `./scripts/verify.sh` run. Only inspect.
- Qdrant health requires a listener on `127.0.0.1:6333`. If there is none, write `qdrant.skipped` and do not call it verified.

- **Validate compose.** Run `docker compose config`. Exit code is `0`. Rendered YAML includes the `qdrant`, `langfuse-web`, and `langfuse-worker` services. Host ports for Qdrant `6333`/`6334` and MinIO `9090` bind to `127.0.0.1`. Langfuse images are `docker.langfuse.com/langfuse/langfuse:4.59.0` and `.../langfuse-worker:4.59.0`, not `:4`.
- **Qdrant liveness (only if listening).** Run `curl -sf http://127.0.0.1:6333/healthz`. HTTP status is 200.
- **Bundled helper.** Run `./scripts/verify.sh`. When Docker exists, `compose.config.out` and `compose.config.exit` are written. When 6333 is closed, `qdrant.skipped` exists instead of a fake 200.
- **Proof.** Keep the compose config output and either Qdrant's health body or the skip file. A skipped Qdrant check is not a verified Qdrant.

## Gotchas

- `docker compose config` does not start containers. A valid config is not proof that Qdrant is healthy.
- Host binds are loopback-only for Qdrant and MinIO. Curling a LAN interface, or assuming `6333` is public, is wrong.
- Do not `docker compose down` during cleanup unless this run started compose (the helper never does).
- The full Langfuse stack is heavy. Do not boot it just to prove the API. Use `docker compose up -d qdrant` when a later issue needs a vector store.
- CI does not run compose. A verification skip for Qdrant in CI is expected.
