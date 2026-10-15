# EDGAR ingest

`capitalcite ingest` looks up CIKs from `company_tickers.json`, lists matching
filings on `data.sec.gov/submissions` (including extra `filings.files` pages),
downloads each primary document, and writes sidecar metadata under the data
directory. Reruns skip files that already exist.

## Sub-features

- `ingest-help` lists `--tickers`, `--forms`, `--years`, and `--output-dir`.
- `ingest-download` stores one primary document plus `metadata.json` per matching filing.
- `ingest-primary-resolve` reads `index.json` and, when the declared primary is a small cover/exhibit shell, stores the largest eligible main 10-K `.htm` instead (excludes exhibits and `R*.htm`).
- `ingest-idempotent` skips a second run of the same tickers/forms/years/output.
- `ingest-metadata` sidecar includes ticker, form, period, accession, and URL.

## How to get to it (user POV)

- Run `uv run capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025`.
- Override the store with `--output-dir` (verification isolates under `artifacts/verify/<timestamp>/data`).
- Default root is `DATA_DIR` (usually `data/`). Layout: `raw/edgar/{ticker}/{form}/{period}/{accession}/`.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `SEC_USER_AGENT` contains an `@` (the helper uses `capitalcite-verify you@example.com`).
- Doctor: `uv run capitalcite ingest --help` exits 0. Live download needs sec.gov reachable from this machine. If SEC returns 403 or the client times out, record `verified-unreachable` with the attempted command. Do not mock EDGAR. Do not treat pytest as this feature.
- Isolate writes with `--output-dir artifacts/verify/$TS/data`. Do not ingest into a developer's `data/` directory.

- **Show help.** Run `uv run capitalcite ingest --help`. Exit code is `0`. Stdout mentions `--tickers`, `--forms`, and `--years`.
- **Download expected 10-Ks.** Run `uv run capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K --years 2023-2025 --output-dir artifacts/verify/$TS/data`. Exit code is `0`. Stdout includes `downloaded=` and `failed=0`. Under that output dir, each of JPM, BAC, WFC, C has `metadata.json` for periods `2023-12-31`, `2024-12-31`, and `2025-12-31` (12 sidecars). Each sidecar has `ticker`, `form`=`10-K`, `period`, `accession`, and `url`. Each sibling primary document is a non-empty file. Wells Fargo primaries are the large 10-K HTML (`wfc-20231231_d2.htm`, `wfc-20241231_d2.htm`, `wfc-20251231.htm`), not the cover-page shells SEC lists as `primaryDocument`.
- **Idempotent rerun.** Run the same command against the same `--output-dir`. Exit code is `0`. Stdout includes `downloaded=0` and `skipped=12` (or skipped equal to the first run's filing count). No extra archive downloads are required for proof; the skipped count and unchanged file set are the proof.
- **Bundled helper.** Run `./scripts/verify.sh`. `ingest.help.out`, `ingest.out` / `ingest.exit` (or `ingest.unreachable`), and `ingest.files.txt` are written under `artifacts/verify/<timestamp>/`.
- **Proof.** Keep the CLI stdout/stderr/exit, `ingest.files.txt`, and the stored `metadata.json` files. A 200 from `/health` is not ingest proof.

## Gotchas

- Live EDGAR. CI pytest uses recorded fixtures and blocks sockets. Verification must use the real client. Cloud datacenter IPs often get HTTP 403; that is unreachable, not a product bug, if the User-Agent was sent.
- `filings.recent` for these banks is about one year of 424B2 noise. FY2023/FY2024 10-Ks come from extra submissions files. A run that only stores FY2025 is a client bug, not "SEC had nothing".
- Primary 10-K HTML is tens of megabytes. Always isolate `--output-dir`. Do not commit `data/raw/`.
- SEC `primaryDocument` is not always the 10-K. WFC FY2025 declares `wfc-20251231_d2.htm` (~360KB shell); the real report is `wfc-20251231.htm`. FY2023/FY2024 invert the names. A stored WFC file under ~5MB is the shell, not the report.
- `CliRunner().invoke(...)` in pytest is not this feature.
- Index, `/ask`, and eval are still later issues. A missing `index` subcommand is expected.
