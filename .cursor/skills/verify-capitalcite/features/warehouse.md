# Quarterly bank-financials warehouse

`dbt build --target ci` turns the committed SEC companyfacts and FDIC
financials fixtures into `dim_bank`, `dim_period`, and
`fct_bank_financials_quarterly`. An analyst asks for one bank, one quarter,
and one measure, and gets a single row with source lineage. `/ask` does not
read this warehouse.

## Sub-features

- `dbt-ci-build` runs the `ci` target against `tests/fixtures/metrics` and exits 0 with `Completed successfully`.
- `dbt-docs` runs `dbt docs generate` on that same target.
- `dbt-dev` reads `data/raw` and writes `data/warehouse/capitalcite.duckdb`. That path is outside this helper unless someone has already extracted snapshots.

## How to get to it (user POV)

- From the repo root, run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`.
- Query `fct_bank_financials_quarterly` joined to `dim_bank` and `dim_period` in the DuckDB file `dbt/target/ci.duckdb`.
- Run `uv run --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci` for the interactive graph. The committed picture is `docs/warehouse-lineage.md`.

## Driving it with the dbt CLI

Preconditions:

- `uv sync --frozen --group metrics` has been run in this checkout, or the command uses `uv run --group metrics`.
- The committed tree `tests/fixtures/metrics` is present.
- Doctor: `GET /health` is 200. This command does not need Qdrant.
- `/ask` is not this feature.

- **Build the fixture warehouse.** Run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`. Exit 0. stdout contains `Completed successfully`.
- **Generate docs.** Run `uv run --group metrics dbt docs generate --project-dir dbt --profiles-dir dbt --target ci`. Exit 0.
- **Bundled helper.** `./scripts/verify.sh` writes `dbt.out`, `dbt.err`, `dbt.exit`, `dbt-docs.out`, `dbt-docs.err`, and `dbt-docs.exit`.
- **Proof.** Keep the CLI stdout, the exit code, and the model and test counts. Pytest is not this feature. A null cell in the fact is shown as null.

## Gotchas

- The top of `verify.sh` runs `uv sync --frozen` without `--group metrics`. The warehouse step uses `uv run --group metrics` so dbt is installed for that command only.
- `ci` reads `tests/fixtures/metrics`. `dev` reads `data/raw`. A missing `dev` extract is not a `ci` failure.
- The committed companyfacts fixtures contain `us-gaap.Assets` only and omit `start`. Holding-company `deposits_usd` and `net_income_quarter_usd` are null on those files.
- FDIC `ASSET`, `DEP`, and `NETINC` stay in thousands of USD. Do not multiply by 1,000 and do not add them to holding-company whole-USD columns.
- `POST /ask` does not read the warehouse.
