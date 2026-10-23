# Quarterly bank-financials warehouse

`dbt build --target ci` turns the committed SEC companyfacts and FDIC
financials fixtures into `dim_bank`, `dim_period`, and
`fct_bank_financials_quarterly`. An analyst asks for one bank, one quarter,
and one measure, and gets a single row with source lineage. `/ask` does not
read this warehouse.

## Sub-features

- `dbt-ci-build` runs the `ci` target against `tests/fixtures/metrics` and exits 0 with `Completed successfully`.
- `dbt-docs` runs `dbt docs generate` on that same target.
- `dbt-ci-shape` checks JPM 2026-06-30 holding-company Deposits and quarterly NetIncomeLoss, and the 2099-09-30 bank NETINC gap.
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
- Set `DBT_PROJECT_DIR` to the `dbt/` directory when the working directory is not the repo root.

- **Build the fixture warehouse.** Run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`. Exit 0. stdout contains `Completed successfully`.
- **Check the real-shape cells.** In `dbt/target/ci.duckdb`, JPM holding company on 2026-06-30 has `deposits_usd` 2500000000000 and `net_income_quarter_usd` 13000000000. JPM bank on 2099-09-30 has `net_income_quarter_usd_thousands` null and `net_income_ytd_usd_thousands` 300000.
- **Generate docs.** Run `uv run --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci`. Exit 0.
- **Bundled helper.** `./scripts/verify.sh` writes `dbt.out`, `dbt.err`, `dbt.exit`, `dbt-docs.out`, `dbt-docs.err`, `dbt-docs.exit`, `dbt-shape.out`, and `dbt-shape.exit`.
- **Proof.** Keep the CLI stdout, the exit code, the model and test counts, and the shape query. Pytest is not this feature. A null cell in the fact is shown as null.

## Gotchas

- The top of `verify.sh` runs `uv sync --frozen` without `--group metrics`. The warehouse step uses `uv run --group metrics` so dbt is installed for that command only.
- `ci` reads `tests/fixtures/metrics`. `dev` reads `data/raw`. A missing `dev` extract is not a `ci` failure.
- A live companyfacts file is multi-megabyte. Unnesting every taxonomy and concept as a view exhausted about 12 GiB and the kernel killed `dbt build --target dev`. Staging now reads only `us-gaap` Assets, Deposits, and NetIncomeLoss in USD, and materialises as a table. `metrics extract` keeps those same three concepts.
- The JPM fixture files both a three-month and a year-to-date `NetIncomeLoss` on the same accession. The grain includes `period_start`. The mart keeps the three-month value. Two Deposits rows share 2026-06-30. The mart keeps the later `filed` value. An 8-K Assets row omits `fp`. `grain_key` coalesces `fiscal_period` so those rows stay unique.
- CERT 628 includes a synthetic 2099 Q1 and Q3 with no Q2 so the missing-prior-quarter path is tested. That year is not a BankFind page. The duration, restatement, and gap tests are enabled on `ci` only. `dev` uses live snapshots and would fail those fixture cells.
- FDIC `ASSET`, `DEP`, and `NETINC` stay in thousands of USD. Do not multiply by 1,000 and do not add them to holding-company whole-USD columns.
- Profile paths resolve from `DBT_PROJECT_DIR`. A bare relative path depends on the working directory and will miss `data/raw` if you launch dbt from `dbt/`.
- `POST /ask` does not read the warehouse.
