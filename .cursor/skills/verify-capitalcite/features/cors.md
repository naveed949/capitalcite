# CORS

`CORS_ORIGINS` is a comma-separated allow-list. `scripts/verify.sh` sets it to `https://verify.example` on the uvicorn it starts. A request whose `Origin` is on that list gets `Access-Control-Allow-Origin` set to that origin. Any other origin gets no such header. An empty list adds no CORS middleware.

## Sub-features

- `cors-allowed` echoes `https://verify.example` on `GET /health`.
- `cors-denied` sends `Origin: https://denied.example` and receives no `Access-Control-Allow-Origin` header.
- `cors-default` with an empty `CORS_ORIGINS` also sends no allow-origin header. The bundled helper does not use the empty default. Pytest covers that case.

## How to get to it (user POV)

- Start uvicorn with `CORS_ORIGINS` set to the origins that may call the API.
- Send `GET /health` with an `Origin` header.
- `./scripts/verify.sh` sets `CORS_ORIGINS=https://verify.example` for the process it starts.

## Driving it with curl

Preconditions:

- A uvicorn this run started is listening on `http://127.0.0.1:${PORT}`.
- That process has `CORS_ORIGINS=https://verify.example`.
- Doctor on that URL has passed.

- **Allow the listed origin.** Run `curl -sS -D artifacts/verify/$TS/cors.allowed.headers -o artifacts/verify/$TS/cors.allowed.body -H 'Origin: https://verify.example' http://127.0.0.1:${PORT}/health`. HTTP status is 200. The header `Access-Control-Allow-Origin` is `https://verify.example`.
- **Deny another origin.** Run the same request with `Origin: https://denied.example` and save `cors.denied.headers`. HTTP status is 200. The response has no `Access-Control-Allow-Origin` header.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `cors.allowed.headers`, `cors.denied.headers`, and exit files. Both curls exit 0.
- **Proof.** Keep the header files. Pytest is not this feature.

## Gotchas

- An empty `CORS_ORIGINS` adds no middleware. A browser then sees no allow-origin header. That is the local default in `.env.example`.
- `CORS_ORIGINS=*` fails at process start. The list is an allow-list, not a wildcard.
- The helper overwrites `CORS_ORIGINS` for the uvicorn it starts. It does not read a developer shell value.
- `Access-Control-Allow-Origin` is the origin string, not `*`. Credentials are not allowed.
- The address check for the rate limit is separate. CORS does not read `X-Forwarded-For`.
