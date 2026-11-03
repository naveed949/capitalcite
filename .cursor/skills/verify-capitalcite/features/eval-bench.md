# Agent vs RAG bench

`capitalcite eval --suite bench` asks each of the 77 v1 recall items and
the 18 metrics items on rag, LangGraph, and a plain tool loop with
`ASK_MODEL=fake`. It writes per-query phases, tokenizer counts, and a
paired bootstrap of graph minus loop.

## Sub-features

- `eval-bench-help` lists `bench` on `capitalcite eval --help`.
- `eval-bench-run` writes `<sha12>-bench.json` (or `<sha12>-dirty-bench.json`) with n, identity, and bottleneck. It does not write a sibling identity file.
- `eval-bench-gate` compares the same-run graph/rag p95 ratio and cost per query to `evals/baselines/bench.json`. When `compile_once` is true it also fails if mean graph `compile_ms` is 0.5 or higher. `verify.sh` runs this gate on the same fake-model suite.

## How to get to it (user POV)

- After `dbt build --target ci`, run `WAREHOUSE_PATH=dbt/target/ci.duckdb METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake uv run capitalcite eval --suite bench`.
- Gate a stored run with `--baseline evals/baselines/bench.json`.
- Rewrite the README bench table with `--readme README.md`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `dbt build --target ci` has written `dbt/target/ci.duckdb`.
- `WAREHOUSE_PATH` points at that file. `METRIC_QUERY_BACKEND=catalog`. `ASK_MODEL=fake`.
- `evals/golden/v1.jsonl` and `evals/golden/metrics.jsonl` are present. This path does not need Docker, live EDGAR, or `OPENAI_API_KEY`.
- Doctor: `uv run capitalcite eval --help` exits 0 and mentions `bench`.

- **Show help.** Run `uv run capitalcite eval --help`. Exit code is `0`. Stdout mentions `bench`.
- **Run the suite.** Run `WAREHOUSE_PATH=dbt/target/ci.duckdb METRIC_QUERY_BACKEND=catalog ASK_MODEL=fake uv run capitalcite eval --suite bench --out $ART/eval-bench`. Exit code is `0`. Stdout includes `suite=bench`, `identity=`, and `bottleneck=`. The out directory contains a `*-bench.json` file.
- **Gate the suite.** Add `--baseline evals/baselines/bench.json`. Exit code is `0`. Stdout includes `eval-baseline ok=true`.
- **Missing warehouse.** Unset `WAREHOUSE_PATH` or point it at a missing file. Exit 2 and `unverified`. That is UNVERIFIED, not a product failure.
- **Bundled helper.** `./scripts/verify.sh` builds the ci warehouse before `capitalcite check`, then writes `eval-bench.out`, `eval-bench.err`, and `eval-bench.exit`. The same command passes `--baseline evals/baselines/bench.json`. `--small` does not skip this.
- **Proof.** Keep the CLI stdout, exit code, and the JSON. Pytest is not this feature.

## Gotchas

- `--suite bench` rejects `--small`, `--chunker`, `--mode`, `--embedder`, `--k` other than 5, and `--print-cache-key`. `--baseline` and `--write-baseline` are the graph/rag p95-ratio, cost, and compile_ms gate. `--paid` is required for `ASK_MODEL=openai` and is never used in CI. tiktoken is in the eval/dev groups; first `o200k_base` use may download the encoding.
- Fake-model generate time is not live model latency. The paid command in `evals/bench/pin.json` is UNVERIFIED.
- `CliRunner().invoke(...)` in pytest is not this feature.
