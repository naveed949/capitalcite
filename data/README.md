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
sidecar after the Qdrant upsert finishes. The points live in Qdrant. The
file is the receipt for that collection name. The name is
`{QDRANT_COLLECTION}-{config_hash}`.

```
data/index/{collection_name}.json
```
