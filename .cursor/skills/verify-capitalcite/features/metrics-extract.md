# Metrics extract

`capitalcite metrics extract` writes holding-company companyfacts and
lead-bank call reports as separate raw files. `--fixtures` copies the
committed tree and does not open a socket. A live run looks up each lead
bank by legal name on `https://api.fdic.gov/banks`.

## Sub-features

- `metrics-help` lists `extract` on `capitalcite metrics --help` and lists `--fixtures` on `capitalcite metrics extract --help`.
- `metrics-fixtures` writes four companyfacts files, four financials files, sidecars, and `lead_banks.json` with no network.
- `metrics-live` is outside this helper. A 301 is exit 2. A name that is not one active exact match is exit 1 and writes nothing.

## How to get to it (user POV)

- Run `uv run capitalcite metrics --help` from the repo root.
- Run `uv run capitalcite metrics extract --tickers JPM,BAC,WFC,C --fixtures --output-dir <dir>`.
- Omit `--fixtures` to fetch SEC companyfacts and FDIC financials. That path needs `SEC_USER_AGENT` and a network.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `SEC_USER_AGENT` contains an email. The helper sets `capitalcite-verify you@example.com`.
- Doctor: `GET /health` is 200. This command does not need Qdrant.
- Pass an isolated `--output-dir`. Do not write into a developer's `data/`.

- **Show help.** Run `uv run capitalcite metrics extract --help`. Exit 0. stdout mentions `--tickers` and `--fixtures`.
- **Copy the committed snapshots.** Run `uv run capitalcite metrics extract --tickers JPM,BAC,WFC,C --fixtures --output-dir artifacts/verify/<timestamp>/metrics`. Exit 0. stdout contains `metrics extract xbrl=4 financials=4`. Each sidecar `sha256` matches the sibling payload.
- **Bundled helper.** `./scripts/verify.sh` writes `metrics.help.out`, `metrics.out`, `metrics.exit`, and `metrics.files.txt`.
- **Proof.** Keep the CLI stdout, the exit code, and the file list. Pytest is not this feature.

## Gotchas

- `--fixtures` has no directory flag. The committed tree is `tests/fixtures/metrics`.
- The holdings name for C is the BankFind legal name `Citibank, National Association`. The graph snapshot still stores the abbreviation `CITIBANK, N.A.`. That abbreviation returned zero active rows on 2026-10-23.
- `rows=` is the count in the response or the fixture file. Do not treat a particular count as the pass check.
- A 301 from BankFind is exit 2. The client does not follow it.
- `ASK_MODEL=fake` does not run this command and does not call FDIC.
