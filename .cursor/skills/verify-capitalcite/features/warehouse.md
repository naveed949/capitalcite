# Quarterly bank-financials warehouse

`dbt build --target ci` turns the committed SEC companyfacts and FDIC
financials fixtures into `dim_bank`, `dim_period`, and
`fct_bank_financials_quarterly`. An analyst asks for one bank, one quarter,
and one measure, and gets a single row with source lineage. `/ask` does not
read this warehouse.

## Sub-features

- `dbt-ci-build` runs the `ci` target against `tests/fixtures/metrics` and exits 0 with `Completed successfully`.
- `dbt-docs` runs `dbt docs generate` on that same target.
- `dbt-ci-shape` checks JPM 2026-06-30 holding-company Assets from the committed companyfacts fixture and the matching lead-bank Call Report cells.
- `dbt-dev` reads `data/raw` and writes `data/warehouse/capitalcite.duckdb`.

## How to get to it (user POV)

- From the repo root, run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`.
- Query `fct_bank_financials_quarterly` joined to `dim_bank` and `dim_period` in the DuckDB file under `dbt/target/ci.duckdb`.
- Run `uv run --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci` for the interactive graph. The committed picture is `docs/warehouse-lineage.md`.
- For local raw snapshots, extract first, then `--target dev`.

## Driving it with the dbt CLI

Preconditions:

- `uv sync --frozen --group metrics` has been run in this checkout, or the command uses `uv run --group metrics`.
- The committed tree `tests/fixtures/metrics` is present.
- Doctor: `GET /health` is 200. This command does not need Qdrant.
- `/ask` is not this feature.

- **Build the fixture warehouse.** Run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`. Exit 0. stdout contains `Completed successfully`. The command does not need `DBT_PROJECT_DIR` when the working directory is the repo root.
- **Check the fixture cells.** In `dbt/target/ci.duckdb`, JPM holding company on 2026-06-30 has `assets_usd` 5015069000000 and null Deposits and quarterly NetIncomeLoss. JPM bank on 2026-06-30 has `assets_usd_thousands` 4091315000 and `net_income_ytd_usd_thousands` 31335000.
- **Generate docs.** Run `uv run --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci`. Exit 0.
- **Bundled helper.** `./scripts/verify.sh` writes `dbt.out`, `dbt.err`, `dbt.exit`, `dbt-docs.out`, `dbt-docs.err`, `dbt-docs.exit`, `dbt-shape.out`, and `dbt-shape.exit`.
- **Proof.** Keep the CLI stdout, the exit code, the model and test counts, and the shape query. Pytest is not this feature. A null cell in the fact is shown as null.

## Gotchas

- The top of `verify.sh` runs `uv sync --frozen` without `--group metrics`. The warehouse step uses `uv run --group metrics` so dbt is installed for that command only.
- `ci` reads `tests/fixtures/metrics`. `dev` reads `data/raw`. A missing `dev` extract is not a `ci` failure.
- A live companyfacts file is multi-megabyte. Unnesting every taxonomy and concept as a view exhausted about 12 GiB and the kernel killed `dbt build --target dev`. Staging now reads only `us-gaap` Assets, Deposits, and NetIncomeLoss in USD, and materialises as a table. `metrics extract` keeps those same three concepts.
- Duration, restatement, 8-K, later-DEF-14A, missing-prior-quarter, and Q4 derivation cases live in dbt `unit_tests` on `int_bank_financials_long` with synthetic given rows. They do not rewrite the committed companyfacts or Call Report snapshots.
- Holding-company Q4 quarterly net income is FY minus the Q3 10-Q nine-month YTD when no three-month Q4 duration was filed. A 10-K reports the annual figure only. The fact stores `net_income_method` as `reported_3m` or `fy_minus_9m` and stores the nine-month accession on derived rows.
- Collapse ranks 10-K/10-Q before other forms, then latest filed, for instants and durations. A later DEF 14A rounded FY does not beat the 10-K.
- `data/warehouse` is in the tree. `metrics extract` also creates it if it is missing. A fresh clone can extract and then run `--target dev` without a manual mkdir.
- FDIC `ASSET`, `DEP`, and `NETINC` stay in thousands of USD. Do not multiply by 1,000 and do not add them to holding-company whole-USD columns.
- Profile paths default `DBT_PROJECT_DIR` to `dbt`. Snapshot globs fall back to the repo root. Set `DBT_PROJECT_DIR` only when the working directory is not the repo root.
- `POST /ask` does not read the warehouse.
