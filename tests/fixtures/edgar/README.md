# Recorded EDGAR fixtures

Captured on 2026-10-16 from:

- `https://www.sec.gov/files/company_tickers.json`
- `https://data.sec.gov/submissions/CIK##########.json`
- extra `filings.files` JSON (columnar, no wrapper)

Ticker rows, CIKs, accessions, report dates, filing dates, and primary-document
names are real. Columnar arrays are trimmed to the 10-Ks (plus a little noise)
needed to exercise listing and extra-file paging. Bodies under `docs/` are
short HTML stubs: live 10-K primary documents are tens of megabytes and are
not committed.

Do not point CI at sec.gov. Replay these files through `httpx.MockTransport`.
