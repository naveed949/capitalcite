# 0026. Metric query tool

Date: 2026-10-23

Status: Accepted

`metric_query` answers one named metric for one ticker and one quarter-end.
The number comes from the MetricFlow semantic layer. The citation comes from
the matching row of `fct_bank_financials_quarterly`.

## The wire is an enum

The gate allow-list names the nine metrics and one group-by,
`metric_time__quarter,bank__ticker`. The other arguments are `ticker`,
`period`, and an optional `entity_level`. `additionalProperties` is false.
`period` is the fact `quarter_id`. `parse_metric_query` rejects a date that
is not a quarter-end and fills `entity_level` from the metric's lock. A
caller cannot pass a filter string or a SQL statement.

`max_calls` is 4. `max_arg_bytes` is 1024.

## Live MetricFlow and a compiled catalog

Local and CI set `METRIC_QUERY_BACKEND=live`. Each call shells `mf query`.
`DBT_TARGET` selects the warehouse. The image sets
`METRIC_QUERY_BACKEND=catalog`. The image build runs `mf query --explain`
once per metric, replaces the sentinel ticker and quarter start with bound
parameters, and checks those bytes against `catalog.json`. Request time
opens DuckDB read-only and runs that statement. The runtime image does not
install MetricFlow. `duckdb` is a dependency of the API. The `metrics`
group stays out of that image.

A catalog whose bytes differ from a fresh compile fails CI. The semantic
hash on the file is the sha256 of `dbt/models/semantic/bank_financials.yml`.

`deposits_qoq_growth` keeps the quarter literal behind `OR 1 = 1`.
MetricFlow applies `where` before the one-quarter offset, so a real time
equality drops the prior quarter and the growth cell is null. The result
keeps the row for the requested quarter start.

## The quote cites the fact

MetricFlow returns the number. The fact row holds the source. FDIC metrics
cite `fdic_repdte` and `fdic_source_url`. Holding-company assets and
deposits cite the matching SEC accession. Holding-company net income cites
the quarterly accession, and also the nine-month accession when
`net_income_method` is `fy_minus_9m`. `deposits_qoq_growth` cites the
current FDIC row. The prior quarter is an input to the expression and is
not a second hit.

The first sentence of the quote ends on the rendered figure. The next
sentence is the unit. `nim` and `efficiency_ratio` repeat that figure with
a percent sign. `ground()` can cite the number because the quote contains it.

## Alternatives that lost

Text-to-SQL lets the model write the filter. A wrong join can add SEC
dollars to FDIC thousands.

A raw SQL tool has the same failure and puts a warehouse path on the wire.

An MCP server over dbt is a second query language and a second process.
The agent already has a gate. This tool goes through that gate.

A request path that never calls MetricFlow cannot show that the baked SQL
still matches `mf`. Live and CI keep the `mf` call. The image keeps the
catalog.
