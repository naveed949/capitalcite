# Hybrid search

`POST /search` returns ranked chunks with scores and citation fields.
`capitalcite index` embeds `parsed/edgar/**/chunks.jsonl` into Qdrant first.
The default embedder is local FastEmbed `bge-small`. `openai-small` reads
`OPENAI_API_KEY`. Verification uses embedded Qdrant (`QDRANT_PATH`), not
Docker.

## Sub-features

- `index-help` lists `--input-dir`, `--chunker`, `--size`, `--overlap`, and `--embedder`.
- `index-build` runs `capitalcite index --embedder bge-small` twice on the ingested corpus and records the same `config_hash`, collection name, and point count.
- `search-post` accepts `query`, `mode`, `k`, `ticker`, `form`, and `period`, and returns `config_hash`, `collection_name`, `point_count`, and hits with `score` plus citation fields.
- `search-golden` posts hybrid (with and without ticker), dense, and bm25 queries for `cc-h-013` and `cc-h-063`. A gold evidence span must overlap a top-k chunk.
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
- Live ingest of at least JPM FY2024 and WFC FY2025, then `capitalcite chunk --chunker section`, must have succeeded. `QDRANT_PATH` is a directory shared by `capitalcite index` and uvicorn. Do not start `docker compose` from this step. If ingest could not run, record `search.unreachable`. Do not mock Qdrant.
- Quote-index sanity needs `evals/golden/v1.jsonl`. If that file cannot load, the step is UNVERIFIED. It must not be reported as corpus recall.

- **Show index help.** Run `uv run capitalcite index --help`. Exit code is `0`. Stdout mentions `--embedder`, `--input-dir`, and `--chunker`.
- **Index.** Copy JPM FY2024, WFC FY2025, and BAC FY2024 chunks into an isolated tree. Run `QDRANT_PATH=<dir> uv run capitalcite index --input-dir <tree> --chunker section --embedder bge-small` twice. Both runs print the same `config_hash`, collection, and `points`.
- **Search.** POST JSON for `cc-h-013` and `cc-h-063` (`mode` `hybrid` with and without `ticker`, plus `dense` and `bm25` for `cc-h-013`) to `http://127.0.0.1:${PORT}/search`. Hybrid with a ticker uses `k` 10; the gold evidence span must overlap a hit. The other queries use `k` 5 and must include the gold `doc_id`. The second hybrid `cc-h-013` after the second index must return the same top-k `(doc_id, start, end)` as the first. HTTP 200 whose hits omit citation fields fails. If ingest was unreachable, the step is UNVERIFIED.
- **Quote-index sanity.** Run `uv run python -m capitalcite.retrieval.sanity`. Exit code `0` prints `recall_at_5=` and `label=sanity`. Exit code `2` is UNVERIFIED. The number is hybrid rank of each handwritten question against its gold quote, indexed as its own chunk. Quotes that share the question's words compete for the five slots.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `index.help.out`, `index.1.out` / `index.2.out`, `search.top5.txt`, per-query `search.*.body.json` (or `search.unreachable`), and `quote-sanity.out` (or `quote-sanity.skipped`).
- **Proof.** Keep the curl status, the response bodies, `search.top5.txt`, and the sanity stdout. A 200 from `/health` is not search proof. Pytest is not this feature.

## Gotchas

- Do not start Docker Compose to make this step pass. Embedded Qdrant (`QDRANT_PATH`) is the verify store. Compose Qdrant on `:6333` stays optional.
- `QDRANT_URL=:memory:` cannot be shared between `capitalcite index` and uvicorn. Use a directory. Embedded Qdrant locks that directory. `scripts/verify.sh` stops uvicorn before `capitalcite index` and starts it after.
- An empty index is a successful HTTP response and a failed proof. `point_count` 0 must not be reported as passed.
- Hits that come back without citation fields, or that miss the gold span, are a product failure, not an unverified environment.
- Quote-index sanity can run with no Qdrant process because it uses an in-memory client and `FakeEmbedder`. A high or low `recall_at_5` there does not fill the README eval table. The 12-filing matrix stays TBD until #6.
- `EMBEDDER=openai-small` without `OPENAI_API_KEY` makes `POST /search` return 503. The local default does not need that key. The first real `capitalcite index` with `bge-small` downloads the FastEmbed model.
- `CliRunner().invoke(...)` and `TestClient` in pytest are not this feature.
