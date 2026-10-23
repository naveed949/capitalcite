# 0019. dbt warehouse

Date: 2026-10-23

Status: Accepted

Build the quarterly bank-financials warehouse in dbt on DuckDB. Staging reads
SEC companyfacts and FDIC financials JSON through target-specific globs. A
hand-kept seed is the only entity list. Analysts query `dim_bank`,
`dim_period`, and `fct_bank_financials_quarterly`. A holding company and its
lead bank are different entity ids and are never summed. `NETINC` stays
year-to-date and is also stored as a de-cumulated quarter. `NIMY` and `EEFFR`
stay as reported.

## Why dbt and DuckDB

The snapshots are already JSON on disk. An analyst asking for JPM assets in
one quarter needs one row, a unit, and a pointer to the filing. That is a
relational grain with tests, not a notebook cell.

dbt is the contract layer. Models name the grain. `schema.yml` and singular
SQL tests fail the build when the grain or the NETINC identity breaks. The
same command runs on a laptop and in CI.

DuckDB reads the JSON in place with `read_json`. There is no server, no
password, and no paid warehouse. The `ci` target points at
`tests/fixtures/metrics`. The `dev` target points at `data/raw` and writes
`data/warehouse/capitalcite.duckdb`, which `/data/**` already gitignores.

A pandas script can unnest the same files. It cannot express unique grain
keys, relationships to dimensions, or accepted `entity_level` values as
build-breaking tests. The next person who adds a ticker has to know which
asserts to copy.

Postgres would give the same SQL surface. It also needs a running server,
migrations, and credentials. The repo is public and the warehouse is a local
analytic file. DuckDB matches that constraint. If a later host wants
Postgres, the models move. The grains stay.

dbt-core 1.12.6, dbt-duckdb 1.11.0, and dbt-metricflow 0.15.0 live in the
`metrics` uv group. They are not in `[project.dependencies]`, not in `dev`,
and not in the runtime image. `Dockerfile` still runs
`uv sync --frozen --no-dev --no-editable`. Python stays below 3.15 because
that is the dbt-core range. `dbt-common` 1.39.0 requires
`protobuf<7,>=6.0`, so the lockfile that includes the metrics group
resolves `protobuf` to 6.33.6. `docs/deploy/image-measure.raw.txt` recorded
`protobuf==7.36.2` for an image build that does not install the metrics
group. That file is a different environment. The semantic layer on this
warehouse is [0025](0025-metricflow-semantic-layer.md).

## Why staging, intermediate, and marts

Staging parses. `stg_sec__facts` types `facts` as JSON and reads only
`us-gaap` Assets, Deposits, and NetIncomeLoss in USD. A live companyfacts
file is several megabytes and thousands of tags. Unnesting every taxonomy
and concept as a view made `dbt build --target dev` exceed 12 GiB and the
kernel killed the process. Concept-scoped `json_extract` plus a table
materialization keeps the same three analyst series and stays in ordinary
laptop memory. `read_json` allows a 64 MiB object so an untrimmed
companyfacts file still parses. `capitalcite metrics extract` now keeps
those same three concepts, so `data/raw` is already slim. `stg_fdic__financials` casts mixed
int and float `NIMY` and `EEFFR` cells to double. Neither staging model
de-cumulates. Neither staging model picks a restated winner.

The intermediate layer owns policy. `int_bank_financials_long` is one row per
entity, quarter, and concept. It collapses duplicate SEC observations, keeps
a filed three-month `NetIncomeLoss` when `period_start` is the inclusive
quarter start, derives holding-company Q4 as FY minus the Q3 10-Q
nine-month YTD when that three-month row is missing, and de-cumulates FDIC
`NETINC`. The unique `(entity, quarter, concept)` test is a real relation
here, not an unpivot invented in a test.

Marts are what an analyst selects. `dim_bank` and `dim_period` are the
join keys. `fct_bank_financials_quarterly` is a pivot of the long model onto
an observed spine. It does not read JSON and it does not run a window.

`int_bank_entity_map` is a seed. Certificate numbers come from a person
checking BankFind, not from a join to `lead_banks.json`. A partial extract
that omits one ticker must not drop a seed row.

## Why the grain is one entity and one quarter

The question is "what were this bank's assets, deposits, net income, NIM, or
efficiency in this quarter?" That is one legal entity and one calendar
quarter-end.

`bank_key` is `ticker|entity_level`. `quarter_id` is the calendar quarter-end
date. A 10-K with `end` 2025-12-31 and a Call Report with `REPDTE` 20251231
share that date. Fiscal `fy` and `fp` stay on the SEC staging row. They are
a second calendar, not the join key.

The holding company and the lead bank are different rows. JPMorgan Chase &
Co. files companyfacts for the parent. JPMorgan Chase Bank, National
Association files a Call Report for certificate 628. Those are different
legal entities, different units, and different regulators. Adding
`assets_usd` to `assets_usd_thousands` is a unit error and a consolidation
error. The fact keeps separate columns so `SUM(assets_usd)` cannot pick up a
lead bank.

The spine is the observed pair. Crossing every seed entity with every
Call Report date would invent holding-company rows on 1984 quarter-ends the
parent never filed. A missing concept on an observed pair stays null. Null
is not zero.

## Why NETINC is de-cumulated here

ADR 0022 records that FDIC `NETINC` is year-to-date cumulative. BankFind
Suite methodologies say Call Reports disclose income and expenses on a
year-to-date basis, and that a quarter is derived by subtracting the prior
Call Report. Source:
https://banks.data.fdic.gov/bankfind-suite/help?helpTopic=disclaimers-and-methodologies

The extract keeps the series cumulative. This warehouse stores both. Q1
copies YTD. A later quarter subtracts the prior observed quarter in the same
calendar year. A missing prior quarter leaves the quarterly value null. The
window runs only on that certificate's own dates, so another bank's quarter
cannot become the prior row.

SEC `NetIncomeLoss` is not de-cumulated from a prior quarter. A 10-Q often
files both the three-month duration and the year-to-date duration under
the same accession, form, fiscal period, and end date. Those two rows are
different facts. The staging grain includes a null-safe `period_start` so
they do not collide. Some 8-K and DEF 14A rows omit `fp`. `fiscal_period`
is null-safe in the same key. The mart keeps the three-month row when it
exists. Instant facts such as Assets and Deposits have a null start. When
two observations share an end date and a start, the window orders `10-K`
and `10-K/A` first, then `10-Q` and `10-Q/A`, then every other form, then
`filed` descending with nulls last, then `accession` descending. That
order applies to instants and durations. A later DEF 14A or 8-K does not
beat a 10-K. JPM DEF 14A
`0000019617-26-000096` files a rounded FY2025 `NetIncomeLoss` of
57,000,000,000. Filed-first would make 2025 Q4 12,977,000,000 instead of
the 10-K 13,025,000,000.

A 10-K reports only the annual figure. Holding-company Q4 is therefore
FY minus the nine-month YTD from the Q3 10-Q, and only when no
three-month Q4 duration was filed. The fact stores `net_income_method`
as `reported_3m` or `fy_minus_9m`, and stores the nine-month accession
when the quarter is derived. The 10-K accession is not a three-month
fact in that filing. The four quarters may not sum to FY when
restatements mix vintages. Four of 68 bank-years do not. WFC 2020 is
one. The warehouse keeps the selected vintage for each quarter. It does
not force the annual identity.

dbt `unit_tests` on `int_bank_financials_long` own those duration,
restatement, 8-K, gap, Q4, and later-DEF-14A cases with synthetic given
rows. The committed companyfacts and Call Report snapshots stay the
extracted files.

## Why reported ratios stay as reported

`NIMY` and `EEFFR` are regulator-reported percents. Recomputing them from
warehouse columns would need interest income, interest expense, earning
assets, and noninterest expense at the same grain. The snapshot does not
carry those inputs. A homemade ratio would look precise and would not match
BankFind. The columns keep the mnemonic and the unit in the name.

## Why the tests are the data contract

`schema.yml` states the grains. Unique and not-null tests on `grain_key`
fail when a duplicate observation survives collapse. Relationships from the
fact to `dim_bank` and `dim_period` fail when a key has no dimension row.
`accepted_values` on `entity_level` is the closed set `holding_company` and
`bank_subsidiary`.

Singular tests name identities the generic tests cannot.

- `assert_no_duplicate_entity_quarter_concept` fails when the long model has
  two rows for the same entity, quarter, and concept.
- `assert_netinc_quarter_continuity` fails when a bank year has four
  continuous quarterly values and those values do not add to the Q4 YTD
  cell. A year with a missing prior quarter is outside that test.
- Unit tests on `int_bank_financials_long` fail when a quarter-plus-YTD
  pair keeps the YTD amount, when a restated instant keeps the earlier
  `filed` value, when an 8-K beats a later 10-Q, when a later DEF 14A
  rounded FY beats a 10-K, when a missing prior Call Report quarter is
  subtracted, or when Q4 is not FY minus the nine-month YTD.

`assert_level_columns_exclusive` fails when one fact row fills both unit
families, or when a `dim_bank` holding row carries a CERT.

The `ci` build is the contract check. `dev` uses the same models on
`data/raw`. Profile paths default `DBT_PROJECT_DIR` to `dbt`. Snapshot
globs fall back to the repo root. The documented command from the repo
root does not need the env var. CI runs that command once with
`DBT_PROJECT_DIR` unset. `data/warehouse` is in the tree so DuckDB can
open the file on a fresh clone. `metrics extract` also creates that
directory if it is missing.

## Alternatives that lost

Pandas scripts. Faster to sketch. No build graph, no grain tests, no
reviewed YAML contract.

Postgres. Same SQL, extra process and credentials. Wrong for a public repo
whose warehouse is a local file.

One shared `assets_usd` column that multiplies FDIC thousands by 1,000.
`SUM(assets_usd)` would then add a parent and a lead bank without a type
error. Separate unit-named columns make that sum impossible.

A cartesian spine of every seed entity and every observed quarter-end.
Certificate 628 has 170 Call Report dates. The parent did not file
companyfacts on those 1984 dates. Observed pairs keep the hole visible.

A fiscal `period_key` built from `fy` and `fp`. SEC `FY` on 2025-12-31 and
FDIC `20251231` would not join. The calendar date does.

Joining `lead_banks.json` as the entity list. A partial extract would drop a
ticker the seed still names. The seed is the list. Staging inner-joins it so
orphan certificate files left on disk do not become entities.

Recomputing NIM and efficiency from warehouse facts. The inputs are not in
the snapshot. The reported mnemonic is.

Incremental models. The snapshots are replaced as a set. Each build rebuilds
the tables. There is no high-water mark.

Putting dbt in the API image or the default `uv sync`. The API does not
query the warehouse. `/ask` is unchanged.

Unnesting every companyfacts concept. The warehouse asks for three series.
The extra tags cost memory and do not change an analyst row.

Leaving `start` out of the SEC grain. Instant and duration facts would
share a key on a 10-Q that files both a quarter and a YTD amount.
