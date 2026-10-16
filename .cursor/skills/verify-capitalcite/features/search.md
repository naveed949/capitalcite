# Hybrid search

`POST /search` returns ranked chunks with scores and citation fields.
`capitalcite index` embeds `parsed/edgar/**/chunks.jsonl` into Qdrant first.
The default embedder is local FastEmbed `bge-small`. `openai-small` reads
`OPENAI_API_KEY`.

## Sub-features

- `index-help` lists `--input-dir`, `--chunker`, `--size`, `--overlap`, and `--embedder`.
- `search-post` accepts `query`, `mode`, `k`, `ticker`, `form`, and `period`, and returns `config_hash`, `collection_name`, `point_count`, and hits with `score` plus citation fields.
- `search-empty` is HTTP 200 with `point_count` 0 when the collection exists and has no points. That response is UNVERIFIED, not a pass.
- `search-quote-sanity` indexes handwritten golden quotes as chunks in memory and prints recall@5. The label is `sanity`. It is not the 12-filing corpus eval.

## How to get to it (user POV)

- Run `uv run capitalcite index --chunker section --embedder bge-small` after `capitalcite chunk`. The default root is `DATA_DIR`.
- POST JSON to `http://127.0.0.1:${PORT}/search`.
- Quote-index sanity is `uv run python -m capitalcite.retrieval.sanity` from the repo root. It does not need Docker or a running Qdrant.

## Driving it with curl

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200, and `uv run capitalcite index --help` exits 0 and mentions `--embedder`.
- A live Qdrant at `QDRANT_URL` and a finished `capitalcite index` are required for a passing `POST /search`. Do not start `docker compose` from this step. If Qdrant is down, the route returns 503. If the collection is empty, the route returns 200 with `point_count` 0. Both are UNVERIFIED. Record `search.unreachable`. Do not mock Qdrant.
- Quote-index sanity needs `evals/golden/v1.jsonl`. If that file cannot load, the step is UNVERIFIED. It must not be reported as corpus recall.

- **Show index help.** Run `uv run capitalcite index --help`. Exit code is `0`. Stdout mentions `--embedder`, `--input-dir`, and `--chunker`.
- **Search.** Run `curl -sS -H 'content-type: application/json' -d '{"query":"CET1 capital ratio","mode":"hybrid","k":5}' "http://127.0.0.1:${PORT}/search"`. HTTP 200 with `point_count` above 0 and hits that include `score`, `chunk.doc_id`, `chunk.section`, `chunk.start`, `chunk.end`, and `chunk.source_url` is a pass. HTTP 503 is UNVERIFIED. HTTP 200 with `point_count` 0, or with no hits, is UNVERIFIED. HTTP 200 whose hits omit those citation fields fails.
- **Quote-index sanity.** Run `uv run python -m capitalcite.retrieval.sanity`. Exit code `0` prints `recall_at_5=` and `label=sanity`. Exit code `2` is UNVERIFIED. The number is hybrid rank of each handwritten question against its gold quote, indexed as its own chunk. Quotes that share the question's words compete for the five slots.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `index.help.out`, `search.body.json` (or `search.unreachable`), and `quote-sanity.out` (or `quote-sanity.skipped`).
- **Proof.** Keep the curl status, the response body, and the sanity stdout. A 200 from `/health` is not search proof. Pytest is not this feature.

## Gotchas

- Do not start Docker Compose to make this step pass. A missing Qdrant is UNVERIFIED, the same way a live EDGAR 403 is UNVERIFIED for ingest.
- An empty index is a successful HTTP response and a failed proof. `point_count` 0 must not be reported as passed.
- Hits that come back without citation fields are a product failure, not an unverified environment.
- Quote-index sanity can run with no Qdrant process because it uses an in-memory client and `FakeEmbedder`. A high or low `recall_at_5` there does not fill the README eval table. The 12-filing matrix stays UNVERIFIED until #6.
- `EMBEDDER=openai-small` without `OPENAI_API_KEY` makes `POST /search` return 503. The local default does not need that key. The first real `capitalcite index` with `bge-small` downloads the FastEmbed model. Verification does not run that download when Qdrant is down, because the route checks the collection before it embeds.
- `CliRunner().invoke(...)` and `TestClient` in pytest are not this feature.
