# Metric query

`metric_query` returns one governed metric for one ticker and one quarter-end.
The number comes from MetricFlow. The citation comes from the matching fact row.
FDIC quotes carry REPDTE and the source URL. SEC quotes carry the accession.
Holding-company net income with method `fy_minus_9m` cites both accessions.

## Sub-features

- `metric-query-live` runs `total_deposits` for JPM at `2026-06-30` through the live backend and returns one hit whose quote contains the figure.
- `metric-query-catalog` compiles the catalog to a temp file and requires the same bytes as `src/capitalcite/metric_query/catalog.json`.
- `metric-query-stream` posts `What were JPM's total deposits in 2026Q2?` to `/ask` and `/ask/stream` with `path=agent` and `model=fake` after `WAREHOUSE_PATH` is set, and requires an answered FDIC citation.
- `metric-query-mcp` lists `metric_query` from `capitalcite mcp` next to `search_filings` and `ask`.

## How to get to it (user POV)

- From the repo root, after `dbt build --target ci`, with `DBT_TARGET=ci`, `DBT_PROJECT_DIR=dbt`, and `DBT_PROFILES_DIR=dbt`.
- The agent path calls the tool when the question names a metric and a ticker. `POST /ask` and `POST /ask/stream` with `path=agent` are that entry.
- MCP lists the tool on `capitalcite mcp`.
- The image sets `METRIC_QUERY_BACKEND=catalog` and `WAREHOUSE_PATH`. That image check is separate.

## Driving it with uv run python

Preconditions:

- `uv sync --frozen` has been run. The compile step shells `uv run --group metrics mf`, so the metrics group must resolve.
- `dbt build --target ci` has written `dbt/target/ci.duckdb`.
- `DBT_TARGET=ci`, `DBT_PROJECT_DIR` pointing at `dbt`, and `DBT_PROFILES_DIR` pointing at `dbt`.

- **Query JPM deposits.** Run the helper below, or the same calls from Python. Exit 0. stdout contains `metric_query ok` and `catalog bytes match`. The hit quote starts with `total_deposits for JPM at 2026-06-30 is` and includes `Unit is thousands of USD.` and `FDIC REPDTE`.
- **Bundled helper.** `./scripts/verify.sh` writes `metric-query.out`, `metric-query.err`, and `metric-query.exit` after the MetricFlow block. It then sets `WAREHOUSE_PATH`, restarts uvicorn, and writes `metric-query-ask.body.json` plus `metric-query-stream.body`. `--small` does not skip this block.
- **Agent stream.** After that restart, `POST /ask/stream` with `path=agent`, `model=fake`, and question `What were JPM's total deposits in 2026Q2?` returns `answered` with one citation whose quote starts with `total_deposits for JPM at 2026-06-30 is` and includes `FDIC REPDTE`.
- **MCP.** `scripts/mcp_probe.py` requires `list_tools` to include `metric_query`.

```bash
uv run python - <<'PY'
import os
from pathlib import Path

from capitalcite.metric_query import open_metric_query, run_metric_query
from capitalcite.metric_query.backends import compile_catalog

root = Path.cwd()
backend, citations = open_metric_query(
    backend_name="live",
    warehouse=root / "dbt" / "target" / "ci.duckdb",
    cwd=root,
    env=os.environ,
)
result = run_metric_query(
    {
        "metric": "total_deposits",
        "group_by": "metric_time__quarter,bank__ticker",
        "ticker": "JPM",
        "period": "2026-06-30",
    },
    backend=backend,
    citations=citations,
)
if not result.ok or len(result.hits) != 1 or result.value not in result.hits[0].chunk.text:
    raise SystemExit(result.detail)
print(f"metric_query ok figure={result.value}")
PY
```

## Gotchas

- An unset `WAREHOUSE_PATH` makes the API tool return `metric query is not configured`. The process still boots.
- A set path whose file is missing fails `open_metric_query` during API startup.
- `period` is the quarter-end `quarter_id`. `metric_time__quarter` is the quarter start. `2026-06-30` is quarter start `2026-04-01`.
- `deposits_qoq_growth` cites the current and prior FDIC rows.
- Holding-company deposits and holding-company net income are null on the committed fixtures.
- The image measure after copying the warehouse is UNVERIFIED when Docker cannot run.
