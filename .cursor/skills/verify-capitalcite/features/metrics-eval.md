# Metrics exact-match eval

`capitalcite eval --suite metrics` asks each pointer gold item on the
agent path and the RAG path with `ASK_MODEL=fake`. Exact match is the
`render_figure` token of a non-null warehouse cell. Refusal is a
separate rate on null cells. Coverage counts how many handwritten
numeric v1 items resolve to a non-null mart cell.

## Sub-features

- `metrics-eval-help` lists `metrics` on `capitalcite eval --help`.
- `metrics-eval-run` writes `<sha12>-metrics.json` with n, exact match, and refusal for both paths, plus `v1_mart`.
- `metrics-eval-readme` rewrites the README metrics table from that JSON.

## How to get to it (user POV)

- After `dbt build --target ci`, run `WAREHOUSE_PATH=dbt/target/ci.duckdb METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake uv run capitalcite eval --suite metrics`.
- Render a stored file with `--from-results evals/results/<sha12>-metrics.json`.
- Rewrite the README table with `--readme README.md`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `dbt build --target ci` has written `dbt/target/ci.duckdb`.
- `WAREHOUSE_PATH` points at that file. `METRIC_QUERY_BACKEND=catalog`. `ASK_MODEL=fake`.
- `evals/golden/metrics.jsonl` is present. This path does not need Docker, live EDGAR, or `OPENAI_API_KEY`.
- Doctor: `uv run capitalcite eval --help` exits 0 and mentions `metrics`.

- **Show help.** Run `uv run capitalcite eval --help`. Exit code is `0`. Stdout mentions `metrics`.
- **Run the suite.** Run `WAREHOUSE_PATH=dbt/target/ci.duckdb METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake uv run capitalcite eval --suite metrics --out $ART/eval-metrics`. Exit code is `0`. Stdout includes `suite=metrics`, `agent_exact=`, `agent_refusal=`, `rag_exact=`, `rag_refusal=`, and `v1_mart=`. The out directory contains a `*-metrics.json` file.
- **Missing warehouse.** Unset `WAREHOUSE_PATH` or point it at a missing file. Exit 2 and `unverified`. That is UNVERIFIED, not a product failure.
- **Bundled helper.** `./scripts/verify.sh` builds the ci warehouse before `capitalcite check`, then writes `eval-metrics.out`, `eval-metrics.err`, and `eval-metrics.exit`. `--small` does not skip this.
- **Proof.** Keep the CLI stdout, exit code, and the JSON. Pytest is not this feature.

## Gotchas

- `--suite metrics` rejects `--small`, `--chunker`, `--mode`, `--embedder`, `--k` other than 5, `--baseline`, `--write-baseline`, and `--print-cache-key`.
- Gold rows store a source pointer, not a typed figure. `resolve` re-derives the expected token from the fixture JSON.
- RAG on the fixture 10-Ks is expected to miss the warehouse token. That is the comparison, not a broken index.
- `capitalcite check` re-runs this suite and fails when a gated hit drops. Changing `evals/baselines/ci.json` needs the `eval-baseline` label.
- `CliRunner().invoke(...)` in pytest is not this feature.
