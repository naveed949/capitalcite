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
