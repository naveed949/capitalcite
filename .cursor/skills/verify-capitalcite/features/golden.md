# Golden Q&A set

The golden set is the committed v1 Q&A file a later eval will score against. `capitalcite golden validate` loads `evals/golden/v1.jsonl`, checks the mix floors, and proves every evidence quote is a substring of a stored passage. It does not call EDGAR, Qdrant, or an LLM.

## Sub-features

- `golden-help` lists the `validate` command, `--path`, and `--against-raw`.
- `golden-validate` exits 0 and prints item, mix, and source counts.
- `golden-grounded` fails if a quote is missing from `evals/golden/passages/`.
- `golden-against-raw` matches handwritten quotes to parsed `document.md` and records start/end spans. FinanceBench and FinQA rows are skipped (answer-scoring only).

## How to get to it (user POV)

- Run `uv run capitalcite golden validate`.
- Override the directory with `--path` (defaults to `evals/golden`).
- Read `evals/golden/README.md` for how items were sourced.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `evals/golden/v1.jsonl` and `evals/golden/passages/` are present. This path does not need uvicorn, Docker, or live EDGAR.
- Doctor: `uv run capitalcite golden validate --help` exits 0.

- **Show help.** Run `uv run capitalcite golden validate --help`. Exit code is `0`. Stdout mentions `--path` and `--against-raw`.
- **Validate the committed set.** Run `uv run capitalcite golden validate --path evals/golden`. Exit code is `0`. Stdout includes `items=` with a value of at least `90`, `unanswerable_or_trick_pct=` at least `0.15`, and `numeric_pct=` at least `0.30`.
- **Align to a local corpus.** After ingest and `capitalcite chunk`, run `uv run capitalcite golden validate --path evals/golden --against-raw $DATA_DIR`. Exit code is `0`. Stdout includes `aligned=` and `recall=handwritten-only`.
- **Bundled helper.** Run `./scripts/verify.sh`. `golden.help.out`, `golden.out`, and `golden.exit` are written under `artifacts/verify/<timestamp>/`. After a live ingest, `golden.against-raw.out` is written. If ingest is unreachable, `golden.against-raw.skipped` is `UNVERIFIED`.
- **Proof.** Keep the CLI stdout and exit code. Pytest is not this feature.

## Gotchas

- This command is offline. A missing passage file is a product bug, not `UNVERIFIED`.
- Live ingest is a different feature. Do not require EDGAR 200s to call the set valid.
- `capitalcite eval --suite golden` is a different feature. This command only validates the set.
- `CliRunner().invoke(...)` in pytest is not this feature.
