# MetricFlow metrics

`mf` compiles a named metric to SQL against `fct_bank_financials_quarterly`.
The metric owns the column, the unit, the aggregation, and the legal entity.
The caller does not write that SQL. `/ask` does not read this warehouse.

## Sub-features

- `mf-validate` runs `mf validate-configs` after `dbt build --target ci` and exits 0.
- `mf-query` runs `total_deposits` and `deposits_qoq_growth` grouped by `metric_time__quarter` and `bank__ticker`, filtered to `bank_subsidiary`, with `--quiet` and `--order metric_time__quarter,bank__ticker`. Exit 0.
- `mf-saved` runs `lead_bank_deposits_qoq`, `lead_bank_balance_sheet`, `lead_bank_earnings`, and `holding_company_assets`. Each exits 0.
- `mf-pytest` runs `pytest tests/test_mf_metrics.py -m mf` and exits 0. The snapshot matches `evals/results/metrics/<sha12>-mf-query.txt`.

## How to get to it (user POV)

- From the repo root, export `DBT_PROJECT_DIR=dbt`, `DBT_PROFILES_DIR=dbt`, and `DBT_TARGET=ci`.
- Run `uv run --group metrics dbt build --project-dir dbt --profiles-dir dbt --target ci`.
- Run `uv run --group metrics mf validate-configs`.
- Run the required query in the next section.
- Run `uv run --group metrics mf query --saved-query lead_bank_deposits_qoq`, and the same command for `lead_bank_balance_sheet`, `lead_bank_earnings`, and `holding_company_assets`.

## Driving it with the mf CLI

Preconditions:

- `uv sync --frozen --group metrics` has been run in this checkout, or the command uses `uv run --group metrics`. The pytest step also needs `--group dev`.
- The committed tree `tests/fixtures/metrics` is present.
- `dbt build --target ci` has written `dbt/target/ci.duckdb` and `dbt/target/semantic_manifest.json`.
- `DBT_TARGET=ci`, `DBT_PROJECT_DIR=dbt`, and `DBT_PROFILES_DIR=dbt`. `mf` has no `--target` flag. It opens the profile target.
- `/ask` is not this feature.

- **Validate the catalog.** Run `uv run --group metrics mf validate-configs`. Exit 0. stdout contains `Successfully validated`.
- **Query lead-bank deposits.** Run the command below. Exit 0. stdout has columns `metric_time__quarter`, `bank__ticker`, `total_deposits`, and `deposits_qoq_growth`. `total_deposits` is FDIC `DEP` in thousands of USD. `deposits_qoq_growth` is `(this quarter - prior quarter) / prior quarter`.

```bash
uv run --group metrics mf query \
  --metrics total_deposits,deposits_qoq_growth \
  --group-by metric_time__quarter,bank__ticker \
  --where "{{ Dimension('bank__entity_level') }} = 'bank_subsidiary'"
```

- **Order the snapshot.** The committed file and `pytest -m mf` add `--quiet` and `--order metric_time__quarter,bank__ticker`. Compare stdout only, after stripping ANSI.
- **Run a saved query.** `lead_bank_deposits_qoq` stores the required question. `lead_bank_balance_sheet` is FDIC `ASSET` and `DEP`. `lead_bank_earnings` is FDIC `NETINC`, `NIMY`, and `EEFFR` at one bank and one quarter. `holding_company_assets` is SEC `us-gaap` `Assets` in whole USD.
- **Bundled helper.** `./scripts/verify.sh` writes `mf-validate.out`, `mf-validate.err`, `mf-validate.exit`, `mf-query.out`, `mf-query.err`, `mf-query.exit`, `mf-saved-<name>.out`, `mf-saved-<name>.err`, `mf-saved-<name>.exit`, `mf-pytest.out`, `mf-pytest.err`, and `mf-pytest.exit`. `--small` does not skip this block.
- **Proof.** Keep the CLI stdout and the exit code. Pytest is the snapshot. A null growth cell means the prior quarter is missing or zero.

## Gotchas

- Unset `DBT_TARGET` selects `dev`, which reads `data/warehouse/capitalcite.duckdb`. The snapshot test exits before any query when `DBT_TARGET` is not `ci`.
- `dbt build` still takes `--target` on the command line. The env var and that flag have to name the same output, or `mf` opens a different file than the build wrote.
- `metric_time__quarter` prints the quarter start. The quarter ending 2026-06-30 prints as `2026-04-01T00:00:00`. The quarter-end is the last day of the month two months later.
- The growth figure is a fraction. `0.05` means five percent. Do not multiply FDIC thousands by 1,000.
- Holding-company deposits and quarterly net income are null on the committed fixtures. `holding_company_assets` is the holding-company series those fixtures can show.
- `nim` and `efficiency_ratio` are the reported percents at one bank and one quarter. A query that drops the ticker returns the max of those percents. That max is not a portfolio NIM and not a portfolio efficiency ratio.
- `mf` calls `update-checker` on startup. On this pin a failed or quiet check does not block the process. No disable variable is set.
