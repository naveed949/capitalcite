# Ask rate limit

`ASK_RATE_LIMIT` caps `POST /ask` and `POST /ask/stream` per client address inside one process. `0` disables the cap. The address is the TCP peer. `X-Forwarded-For` is ignored. The request after the cap is HTTP 429 with `Retry-After` and a JSON `detail`. `GET /health` is not counted. A second API process has its own counter.

## Sub-features

- `limit-ask` on a uvicorn with `ASK_RATE_LIMIT=2` returns a non-429 for the first two `POST /ask` calls and 429 for the third.
- `limit-stream` returns 429 JSON for `POST /ask/stream` once that same window is full. The body is not `text/event-stream`.
- `limit-health` returns 200 for `GET /health` before those calls and after the 429s.

## How to get to it (user POV)

- Start a second uvicorn with `ASK_RATE_LIMIT=2` and its own `QDRANT_PATH`. Leave the main verify process unlimited so later `/ask` proofs still run.
- POST JSON `{"question":"What is the tier 1 capital buffer?","model":"fake"}` to `/ask` and `/ask/stream`.
- `./scripts/verify.sh` starts that second process, records the codes, and stops it.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- The main uvicorn is already up and is not the process under test. Do not set `ASK_RATE_LIMIT` on it.
- The limited process uses `ASK_RATE_LIMIT=2`, `ASK_RATE_WINDOW_S=60`, `ASK_MODEL=fake`, and a `QDRANT_PATH` that the main process is not using.

- **Leave health unlimited.** `GET /health` four times. Each HTTP status is 200.
- **Fill the window on `/ask`.** `POST /ask` twice. Neither status is 429. The third `POST /ask` is 429. `Retry-After` is an integer of at least 1. The body is JSON with a non-empty `detail`.
- **Stream stays JSON.** `POST /ask/stream` with the same JSON body. HTTP status is 429. `Content-Type` is not `text/event-stream`. `Retry-After` is an integer of at least 1.
- **Health after the cap.** `GET /health` again. HTTP status is 200.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `limit.ask.3.http`, `limit.stream.http`, `limit.stream.body`, and `limit.health.after.http`.
- **Proof.** Keep those files. A 429 from the main verify process is a failed proof, because that process stays unlimited.

## Gotchas

- The counter is not shared across processes. Two replicas each allow `ASK_RATE_LIMIT` requests.
- The helper must give the limited uvicorn its own `QDRANT_PATH`. Embedded Qdrant allows one open at a time on a directory.
- `ASK_RATE_LIMIT=0` is the local default. The image sets `60`.
- `TRUSTED_PROXY_HOPS` defaults to 0. That uses the TCP peer. Set it to the number of trusted reverse proxies in front of the app. The key is then the X-Forwarded-For entry N positions from the right. A short header falls back to the peer.
- The limiter drops the oldest address once it holds about 10_000 keys.
