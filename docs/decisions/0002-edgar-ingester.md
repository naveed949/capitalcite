# 0002. EDGAR ingester

Date: 2026-10-16
Status: Accepted

Issue #2 is the first real I/O in this repo: look up CIKs, list 10-K/10-Q
filings, download primary documents, and keep enough metadata that later
chunkers can cite ticker, form, period, accession, and URL. This record is
why the client, the on-disk layout, and the tests look the way they do.

## Talk to EDGAR ourselves, do not wrap a downloader

`sec-edgar-downloader` and similar libraries already fetch 10-Ks. We did not
use them.

The constraint is not "get some HTML onto disk." It is "own the fair-access
behavior, the metadata schema, and a test seam that never opens a socket in
CI." A wrapper hides User-Agent, pacing, retry, and the extra `filings.files`
pages. Those are the parts that fail first (403, 429, missing FY2023 10-Ks).
httpx is already in the FastAPI stack; ADR 0001 said EDGAR waits should
release the event loop. One `EdgarClient` with an injectable transport is
less code than teaching a third-party library our store layout.

The bulk nightly ZIP (`submissions.zip`) is the efficient way to fetch every
filer's history. Four tickers do not justify unpacking that archive on every
laptop ingest. If the corpus grows to "all BHCs," revisit bulk. Not this issue.

## Filter on period (reportDate), not filingDate

`--years 2023-2025` means fiscal years of the filing, the thing a later
question like "JPM CET1 at year-end 2024" will name. A 10-K for FY2024 has
`reportDate=2024-12-31` and is filed in February 2025. Filtering on
`filingDate` year would drop that 10-K from `--years 2024` and pick it up
under 2025, which is the wrong object.

`reportDate` empty (rare for 10-K/10-Q) falls back to `filingDate` so we
still have a period to put on disk. Forms match case-insensitively but are
stored as SEC sent them. `10-K/A` is not `10-K`.

## Extra submissions files are required

`data.sec.gov/submissions/CIK##########.json` puts at least a year, or 1,000
filings, in `filings.recent`. JPM, BAC, and C flood that window with 424B2
prospectus supplements, so `recent` is about one year of filings even when
the array is tens of thousands of rows. On 2026-10-16, `recent` held the
FY2025 10-K and not FY2023 or FY2024.

Older rows live in `filings.files[]`, each a columnar JSON at
`https://data.sec.gov/submissions/{name}`. We fetch an extra file when its
`filingFrom`/`filingTo` overlaps `[min(year)-01-01, max(year)+1-12-31]`. The
plus-one year is the 10-K filing lag (FY2025 filed February 2026). Files
whose window is entirely outside that range are not fetched. Accessions are
deduped if they appear in both `recent` and an extra file.

The alternative — "recent is enough" — silently under-ingests the years in
the issue. Scanning every extra file for JPM is more HTTP than three 10-Ks
need, but it is the API the issue named, and it stays under 10 req/s.

## Filesystem sidecars, not a catalog database

Each filing is:

```
{data_dir}/raw/edgar/{ticker}/{form}/{period}/{accession}/
  {primaryDocument}
  metadata.json
```

`metadata.json` is ticker, CIK, form, period, filing date, accession, source
URL, sha256, and byte size. The document bytes are the raw primary HTML, not
a parsed tree. Chunking (#3) is the next issue; ingest must not pretend to
understand 10-K structure.

A SQLite catalog would make "what did we download?" a query. It would also
be a second source of truth that can drift from the files. Sidecars sit next
to the bytes they describe. Idempotency is "both files exist"; a rerun does
not GET the archive URL again. That is the fair-access move: 10-Ks are tens
of megabytes. Re-downloading them to refresh a row is how you get blocked.

`DATA_DIR` (default `data/`) is gitignored except this README. `--output-dir`
overrides it so verification can isolate a run under `artifacts/verify/`.

## Fair access is a client invariant

SEC fair-access (https://www.sec.gov/os/accessing-edgar-data):

1. Declared `User-Agent` with a contact email. Settings already refuse to
   boot without `@`. The client sends that value on every request, including
   extra files and document downloads.
2. At most 10 requests per second, all machines counted together. A process
   local limiter spaces acquires by 100ms so we cannot burst 11 into one
   second. We do not try to coordinate across hosts; one ingest job is the
   unit.
3. Retry with backoff on 429 and 5xx, honoring `Retry-After` when present
   (capped). 403 is not retried: that is a block, not a blip. Transport
   errors retry. After five attempts the CLI exits 2 so verification can
   tell "SEC unavailable" from a bug.

httpx handles gzip. Timeouts are 60s because a 10-K can be large.

## Recorded fixtures, no live network in CI

CI running this command against sec.gov would be flaky, slow, and a fair-
access problem of our own. Tests replay trimmed JSON captured from the live
endpoints (real CIKs, accessions, dates, document names) plus a stub HTML
body. `httpx.MockTransport` is the seam; pytest also patches
`HTTPTransport.handle_request` so a forgotten mock fails closed instead of
leaking to the network.

VCR.py would record full 10-K bodies. We do not want those in git. Hand-
trimmed columnar JSON plus one stub HTML is the fixture we can review.

## Consequences

- `capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025`
  is a real command. It will hit sec.gov, write under `data/raw/edgar/`, and
  skip on rerun.
- #3 reads sidecars and primary HTML from this tree. Do not rename the
  layout without another ADR.
- Verification of ingest is live EDGAR. Datacenter IPs sometimes get 403;
  that is `verified-unreachable`, not a mock.
- Regulatory PDFs (FFIEC, Fed, OCC) are not this issue.

## Alternatives considered

| Choice | Why not |
|---|---|
| `sec-edgar-downloader` | Hides User-Agent, pacing, extra files, metadata schema |
| Bulk `submissions.zip` | Right for all filers; overweight for four tickers |
| SQLite/Postgres catalog | Second source of truth before we can query it |
| Filter on `filingDate` | Mis-assigns FY 10-Ks across calendar years |
| Trust `filings.recent` only | Drops FY2023/FY2024 10-Ks for these issuers |
| Live HTTP in pytest | Flaky, slow, violates fair-access in CI |
