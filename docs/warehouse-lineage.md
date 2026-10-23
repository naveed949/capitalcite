# Warehouse lineage

Committed picture of the quarterly bank-financials DAG. `dbt docs generate`
writes the interactive graph under `dbt/target/`. This file is the reviewable
copy.

```mermaid
flowchart LR
  secJson[SEC companyfacts JSON] --> stgSec[stg_sec__facts]
  fdicJson[FDIC financials JSON] --> stgFdic[stg_fdic__financials]
  seed[int_bank_entity_map] --> dimBank[dim_bank]
  seed --> stgSec
  seed --> stgFdic
  stgSec --> dimPeriod[dim_period]
  stgFdic --> dimPeriod
  stgSec --> longModel[int_bank_financials_long]
  stgFdic --> longModel
  seed --> longModel
  longModel --> fact[fct_bank_financials_quarterly]
  dimBank --> fact
```

`dev` reads `data/raw`. `ci` reads `tests/fixtures/metrics`. The DuckDB file
for `dev` is `data/warehouse/capitalcite.duckdb`. The DuckDB file for `ci` is
`dbt/target/ci.duckdb`.
