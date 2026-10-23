# Local data

`capitalcite ingest` writes raw EDGAR primary documents here. Contents are
gitignored; this file stays so the directory exists on a fresh clone.

Layout after `capitalcite ingest --tickers JPM --forms 10-K --years 2024`:

```
data/raw/edgar/{ticker}/{form}/{period}/{accession}/
  {primaryDocument}   # HTML from EDGAR archives
  metadata.json       # ticker, cik, form, period, filing_date, accession, url, sha256, bytes
```

`period` is the filing's `reportDate` (fiscal period end). Reruns skip a folder
when both the document and `metadata.json` are already present.

`capitalcite chunk` reads that tree and writes derived files:

```
data/parsed/edgar/{ticker}/{form}/{period}/{accession}/
  document.md     # cleaned text, tables as markdown
  chunks.jsonl    # one chunk per line, with citation fields
  stats.json      # count and mean tokens for the selected chunker
```

`capitalcite index` reads `parsed/edgar/**/chunks.jsonl` and writes one
sidecar after the Qdrant upsert finishes. The points live in Qdrant
(remote `QDRANT_URL`, embedded `QDRANT_PATH`, or process-local
`QDRANT_URL=:memory:`). The file is the receipt for that collection name.
The name is `{QDRANT_COLLECTION}-{config_hash}`.

```
data/index/{collection_name}.json
```

`capitalcite metrics extract` writes companyfacts and lead-bank call reports.
A rerun replaces the files it names. It does not delete a certificate file
the new link no longer names.

```
data/raw/xbrl/{ticker}.json
data/raw/xbrl/{ticker}.metadata.json
data/raw/fdic/financials/{cert}.json
data/raw/fdic/financials/{cert}.metadata.json
data/raw/metrics/lead_banks.json
```

`lead_banks.json` maps ticker to certificate and holds no dollar amounts.
Each sidecar has `url`, `fetched_at`, `sha256`, and `bytes`. `sha256` is the
hash of the sibling payload. `--fixtures` writes this same layout from
`tests/fixtures/metrics` at the repository root and does not open a socket.

FDIC `ASSET`, `DEP`, and `NETINC` are thousands of USD. `NIMY` and `EEFFR`
are percent. `NETINC` is year-to-date cumulative. SEC companyfacts values
in USD units are whole USD.

`dbt build --project-dir dbt --profiles-dir dbt --target dev` reads
`data/raw` and writes `data/warehouse/capitalcite.duckdb`. `/data/**`
already gitignores that file. The `ci` target reads
`tests/fixtures/metrics` and writes `dbt/target/ci.duckdb`. See
[ADR 0019](../docs/decisions/0019-dbt-warehouse.md).
