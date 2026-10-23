# 0025. MetricFlow semantic layer

Date: 2026-10-23

Status: Accepted

Analysts query named metrics on `fct_bank_financials_quarterly` through
`mf`. One semantic model, `bank_financials`, holds the SEC measures and
the FDIC measures. Each simple metric filters `entity_level`. Balances
keep the latest quarter per bank. Reported rates stay as reported. A
daily time spine supports quarter offsets.

## What a named metric buys

A hand-written query repeats the column, the unit, the aggregation, and
the legal entity. A named metric keeps those choices in one place.
`total_deposits` is lead-bank FDIC `DEP` in thousands of USD, column
`deposits_usd_thousands`. The caller asks for the name. `mf` compiles
the SQL.

The fact is one row per `bank_key` and calendar quarter-end. `bank_key`
is `ticker|entity_level`. SEC columns are whole USD and null on bank
rows. FDIC columns are thousands or percents and null on
holding-company rows. `entity_level` is never an aggregation key. The
filter on each simple metric is applied before aggregation, so
`total_deposits` cannot see holding-company rows.

## Additive, semi-additive, and non-additive

Quarterly net income is a flow. Summing quarters adds the flows. A null
quarter stays null. The sum does not treat that null as zero.

Assets and deposits are balances. A sum of four quarter-end balances is
not a yearly asset figure. Those measures use `agg: sum` and
`non_additive_dimension` on the time dimension. `window_choice` is
`max`. `window_groupings` is `bank`. A quarter query returns that
quarter's balance. A wider window keeps each bank's latest quarter,
then sums banks.

`NIMY` and `EEFFR` are reported percents. They are not summed and not
averaged. The measure `agg` is `max`, with the same non-additive window
as the balances. At one bank and one quarter the cell is the reported
percent. A query that drops the ticker returns the max of those
percents. That max is not a portfolio NIM and not a portfolio efficiency
ratio. MetricFlow 0.213 cannot mark a measure as not aggregatable.

## How MetricFlow compiles a query

MetricFlow joins the semantic model to the day spine and applies the
metric filter before aggregation. Shared entity names are the join
graph. Here the only entity is `bank`, expr `bank_key`, so the graph
has one model.

`deposits_qoq_growth` is a derived metric. `offset_window: 1 quarter`
shifts `total_deposits` along the spine. The expression is
`(deposits - deposits_prev) / nullif(deposits_prev, 0)`. A missing
prior quarter, or a prior quarter of zero, stays null. There is no
`fill_nulls_with` and no `join_to_timespine`.

DuckDB rewrites a one-quarter offset as `count * 3` months. Three
months before a quarter-end is not the prior quarter-end. 2026-06-30
minus three months is 2026-03-30, not 2026-03-31. The time dimension
expr is the quarter start of `quarter_id`, the same dates
`calendar_quarter_start` returns. 2026-04-01 minus three months is
2026-01-01. `metric_time__quarter` therefore prints the first day of
the quarter. The fact still stores the quarter-end.

The spine model is `metricflow_time_spine`. It is every calendar day
from 1984-01-01 through 2030-12-31. dbt-core 1.12.6 rejects a project
whose finest time spine is coarser than a day. `dim_period` stays the
observed quarter-ends. The spine is not a parent of the fact.

## Why the profile target is an environment variable

`mf` 0.15.0 has no `--target` flag. It opens whichever output
`profiles.yml` names. The profile reads `DBT_TARGET` and defaults to
`dev`. CI and the snapshot export `DBT_TARGET=ci` after
`dbt build --target ci`. The dev file and the ci file stay on different
paths. The snapshot test exits before it runs a query when
`DBT_TARGET` is not `ci`.

## Alternatives that lost

Cube is another service and another query language for a local DuckDB
file.

A plain dbt model per metric makes each new growth figure another
model, and copies the entity-level rule into each SQL file.

Hand-written SQL does not stop a query from adding `assets_usd` to
`assets_usd_thousands`.

Two semantic models, one per `entity_level`, still share the entity
name `bank`. MetricFlow joins models that share an entity. A later
edit that sets the entity expr to `ticker` would place FDIC thousands
beside SEC whole dollars. One model keeps a single join graph. The
metric filter is the guard.

Recomputed NIM needs interest income, interest expense, and earning
assets. Recomputed efficiency needs noninterest expense and revenue.
Those inputs are not in the snapshot. Deposits over assets would sum
two semi-additive stocks across the time window. Net income over
assets would divide a flow by a stock. Any SEC amount divided by an
FDIC amount mixes units and legal entities.
