# 0026. Metric query tool

Date: 2026-10-23

Status: Accepted

`metric_query` answers one named metric for one ticker and one quarter-end.
The number comes from the MetricFlow semantic layer. The citation comes from
the matching row of `fct_bank_financials_quarterly`. An analyst who asks
"What were JPM's total deposits in 2026Q2?" gets the warehouse cell and the
FDIC or SEC source for that row, not a span of filing text that happens to
contain a nearby number.

## Why the arguments are typed enums

The planner is untrusted. It can invent a tool name and an argument object.
A text-to-SQL tool would take a string the model wrote and send it to DuckDB.
That string is an injection sink. It is also a join-path sink. The warehouse
stores SEC dollars and FDIC thousands on the same fact table, keyed by
`entity_level`. A free-form filter can add those units together. The review
then has to read every generated statement.

Typed enums shrink the wire to a closed set. `metric` is one of the nine
names in `bank_financials.yml`. `group_by` is the one grain the semantic
layer supports for a single-bank quarter cell,
`metric_time__quarter,bank__ticker`. The other allowed keys are `ticker`,
`period`, and an optional `entity_level`. `additionalProperties` is false.
There is no filter string and no SQL string.

The reviewable surface is `tools.json` plus `parse_metric_query`. A reviewer
reads those two files and knows every call the runner can receive. A
text-to-SQL review reads a new statement on every request.

The same inputs produce the same cell. The catalog SQL is parameterized
with bound values. Live MetricFlow receives the same ticker, quarter start,
and locked `entity_level`. A model paraphrase cannot change the join or the
unit. `period` is the fact `quarter_id`. A date that is not a quarter-end
is a parse error. `parse_metric_query` fills `entity_level` from the
metric's lock, so a caller cannot ask `total_deposits` at the holding
company.

`max_calls` is 4. `max_arg_bytes` is 1024.

## Why the demo runs a precompiled catalog

MetricFlow 0.213 pulls protobuf 7 and a compiler stack. The public demo
image is a 512 MB cgroup. The stored image is already near 220 MiB before
that stack. Installing the `metrics` group at request time would fight the
API's protobuf 6 pin, grow the image, and add a multi-second cold compile
on the first ask.

The image therefore sets `METRIC_QUERY_BACKEND=catalog`. The image build
runs `mf query --explain` once per metric against sentinel ticker `ZZQZZQ`
and quarter start `1999-10-01`, replaces those quoted sentinels with bound
parameters, and writes `catalog.json`. Request time opens DuckDB read-only
and runs that statement. The runtime image does not install MetricFlow.
`duckdb` is already a dependency of the API.

A catalog whose bytes differ from a fresh compile fails CI. The semantic
hash on the file is the sha256 of `dbt/models/semantic/bank_financials.yml`.
Local and CI set `METRIC_QUERY_BACKEND=live` and shell `mf query` on each
call. `DBT_TARGET` selects the warehouse. A test compares the catalog cell
to the live cell on JPM, BAC, WFC, and C. Drift in the YAML, the compiler,
or the committed SQL fails that test.

`deposits_qoq_growth` keeps the quarter literal behind `OR 1 = 1`.
MetricFlow applies `where` before the one-quarter offset, so a real time
equality drops the prior quarter and the growth cell is null. The result
keeps the row for the requested quarter start.

A request path that never called MetricFlow could not show that the baked
SQL still matches `mf`. Live and CI keep the `mf` call. The image keeps
the catalog.

## Why the gate sits at the boundary

ADR 0009 already made `Gate.execute` the only way to run a tool. The
planner, the MCP host, and a hostile retrieved document all choose a name
and an object. The gate is the process boundary for that choice.

Budget, size, and schema denies happen before the runner. An unknown metric
is `invalid_args`. An extra key such as `sql` is `invalid_args`. A fifth
call in one request is `budget_exceeded`. An argument object over 1024
bytes is `args_too_large`. The registry is not called. DuckDB is not
opened. The deny is the audit row.

Putting those checks in the runner would leave a second path. MCP
constructs a new Gate per `tools/call`. HTTP `path=agent` constructs one
Gate per request. Both load the same `tools.json`. A schema deny on MCP
is the same deny on the agent.

The runner still parses. The gate is a JSON Schema subset and cannot lock
`entity_level` to the metric. `parse_metric_query` does that after an
allow. A gate deny never reaches that parse.

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
a percent sign. `ground()` can cite the number because the quote contains
it.

## Alternatives that lost

Text-to-SQL with a validator still lets the model choose the join, the
grain, and the unit. The validator has to re-encode the semantic layer.
A missed rule is a silent wrong number. The reviewable surface becomes
the validator, which grows with every new metric.

A raw read-only SQL tool has the same failure and puts a warehouse path
on the wire. Read-only does not stop a cross-entity sum. It also does
not produce a citation. The tool would return a grid and leave the model
to invent a source.

An MCP server over dbt is a second query language and a second process.
The agent already has a gate. This tool goes through that gate. MCP
lists `metric_query` next to `search_filings` and `ask`. The host does
not speak MetricFlow.

A request path that only ran the catalog could not prove the baked SQL
against live `mf`. The two backends stay. The image uses one. Local and
CI use the other. CI fails when they disagree.
