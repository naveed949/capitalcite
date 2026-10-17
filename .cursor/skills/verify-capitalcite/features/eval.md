# Retrieval eval

`capitalcite eval --suite golden` scores handwritten gold against a live
index. A hit is relevant when the chunk accession matches and the
half-open spans overlap. The command writes JSON and a markdown table
keyed by `config_hash`. `--small` is the CI path. It uses the fixture
corpus, `FakeEmbedder`, and in-memory Qdrant.

## Sub-features

- `eval-help` lists `--suite`, `--small`, `--out`, `--chunker`, `--mode`, `--from-results`, `--baseline`, `--write-baseline`, and `--print-cache-key`.
- `eval-small` runs the matrix on `tests/fixtures/chunking/corpus` with no model download.
- `eval-corpus` re-chunks the twelve ingested 10-Ks, embeds with `bge-small`, and scores dense, BM25, and hybrid on section and fixed.
- `eval-report` prints recall@k, MRR, and nDCG from the JSON. README numbers come from that file.

## How to get to it (user POV)

- Run `uv run capitalcite eval --suite golden --small` from the repo root.
- After ingest, run `QDRANT_PATH=<dir> uv run capitalcite eval --suite golden --input-dir <data> --embedder bge-small`.
- Rewrite the README table with `make eval-report`. The target uses this commit's `evals/results/<sha12>.json` when present, else the directory. `load_reports` selects by recorded `git_sha`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `uv run capitalcite eval --help` exits 0 and mentions `--suite`, `--small`, and `--baseline`.
- `--small` needs `evals/golden/` and `tests/fixtures/chunking/corpus`. It does not need Docker, live EDGAR, or a FastEmbed download.
- The corpus matrix needs a successful ingest of the twelve 10-Ks. If ingest could not run, record `eval.skipped`. Do not mock Qdrant or the embedder.

- **Show help.** Run `uv run capitalcite eval --help`. Exit code is `0`. Stdout mentions `--suite`, `--small`, `--from-results`, and `--baseline`.
- **Small matrix.** Run `uv run capitalcite eval --suite golden --small --out $ART/eval-small`. Exit code is `0`. Stdout includes `items=` and a markdown table with `Recall@5`. The out directory contains a `*-small.json` file with `config_hash` on every arm.
- **Corpus matrix.** After ingest, run `uv run capitalcite eval --suite golden --input-dir $INGEST_DIR --out $ART/eval --chunker section --chunker fixed --embedder bge-small --k 5` with `QDRANT_PATH` set to a directory this run owns. Exit code is `0`. Stdout includes the six-row table (two chunkers, three modes), a `cc-h-013` probe line, and an Agent vs RAG recall line on comparison/multi-hop items labelled `fake/heuristic planner`. Exit 2 is UNVERIFIED. Answer-quality cells stay UNVERIFIED.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `eval.help.out`, `eval-small.out`, and either `eval.out` plus `eval.table.md` or `eval.skipped`.
- **Proof.** Keep the CLI stdout, exit code, and the JSON. Pytest is not this feature. Quote-index sanity is not this feature.

## Gotchas

- `--small` rejects `--embedder`, `--input-dir`, and `--baseline`. That is a product error, not UNVERIFIED.
- Nightly CI ingests the twelve 10-Ks, caches them, and runs `eval --suite golden --chunker section --mode hybrid --embedder bge-small --baseline evals/baselines/nightly.json`. The Qdrant cache key comes from `--print-cache-key` (IndexConfig hash plus sorted accession ids). Cold embed may take up to 360 minutes; a restore must finish in 20. A missing `data/` tree is not a skip. Ingest exit 2 (EDGAR 403/429/5xx) is UNVERIFIED. Do not add that compare to `verify.sh`. The six-arm README table is not the nightly gate. Eval logs `chunk_s` / `embed_s` / `query_s`.
- The corpus run re-chunks into a private tree. It does not reuse `data/parsed` from an earlier `capitalcite chunk`.
- RAGAS and hybrid+rerank are UNVERIFIED. Do not invent those numbers.
- `CliRunner().invoke(...)` in pytest is not this feature.
