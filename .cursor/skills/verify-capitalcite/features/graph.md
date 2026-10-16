# Optional entity graph

`capitalcite graph` loads a recorded BHC → subsidiary → filing → metric
snapshot and runs typed `graph_query` lookups. Blank `NEO4J_URI` does not
change `/health`, `POST /search`, or RAG `POST /ask`. Live Neo4j is
optional compose profile `graph`.

## Sub-features

- `graph-help` lists `load` and `query` on `capitalcite graph --help`.
- `graph-load-memory` MERGE-loads `evals/golden/graph_snapshot.json` twice and prints the same `banks=` count.
- `graph-query-memory` returns RSSD `852218` for `--kind subsidiaries --ticker JPM`.
- `graph-eval-small` runs `capitalcite eval --suite graph --small` and prints `graph_exact`.
- `graph-neo4j` is UNVERIFIED when Docker or `NEO4J_URI` is missing.

## How to get to it (user POV)

- Run `uv run capitalcite graph load --backend memory` from the repo root.
- Run `uv run capitalcite graph query --kind subsidiaries --ticker JPM`.
- Optional: `docker compose --profile graph up -d neo4j`, set `NEO4J_URI=bolt://localhost:7687`, then `--backend neo4j`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `GET /health` is 200. The graph CLI does not need Qdrant.
- Do not start Neo4j as part of the default verify run.

- **Load the snapshot in memory.** Run `uv run capitalcite graph load --backend memory`. Exit 0. stdout contains `backend=memory` and `banks=8`.
- **Query a subsidiary.** Run `uv run capitalcite graph query --kind subsidiaries --ticker JPM`. Exit 0. stdout contains `852218`.
- **Score graph vs hybrid.** Run `uv run capitalcite eval --suite graph --small`. Exit 0. stdout contains `graph_exact=` and `hybrid_hit_rate=`.
- **Neo4j without URI.** Run `uv run capitalcite graph load --backend neo4j` with empty `NEO4J_URI`. Exit 2 and `unverified`. That is UNVERIFIED live Bolt, not a product failure.
- **Bundled helper.** `./scripts/verify.sh` writes `graph.help.out`, `graph.load.out`, `graph.query.out`, and `eval-graph-small.out`. Missing Docker writes no Neo4j start.
- **Proof.** Keep the CLI stdout and exit codes. Pytest is not this feature.

## Gotchas

- `docker compose up -d` does not start Neo4j. The service uses profile `graph`.
- `graph_query` on `POST /ask` with empty `NEO4J_URI` returns `graph not configured`. That does not fail the request.
- RSSD questions are in `evals/golden/graph.jsonl`, not `v1.jsonl`. Do not expect them in the 77-item recall table.
