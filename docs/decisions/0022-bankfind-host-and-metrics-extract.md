# 0022. BankFind host and metrics extract

Date: 2026-10-23

Status: Accepted

Point institutions lookup at `api.fdic.gov/banks` and keep a 301 fail-closed.
`capitalcite metrics extract` stores holding-company companyfacts and
lead-bank call reports as separate raw snapshots. Certificate numbers come
from a live name lookup. `--fixtures` copies committed bytes and does not
open a socket.

## Keep a 301 fail-closed

`HttpxJsonClient` sets `follow_redirects=False`. A replayed 301 raises
`HttpStatusError` with status 301 after one request. A transport failure
raises `HttpTransportError`. The metrics command maps those types to exit 2.
It does not inspect message prefixes.

Institutions are `https://api.fdic.gov/banks/institutions`. Financials are
`https://api.fdic.gov/banks/financials`. The ask route still builds one SEC
client with `shared_limiter` and `SEC_USER_AGENT`, and one FDIC client with
timeout `30` seconds and User-Agent `capitalcite/<version>`. The FDIC client
does not send the SEC contact email. `ASK_MODEL=fake` still does not call
either host.

## Write two series from one command

A holding company has a ticker, a 10-digit CIK, and the lead bank's legal
name. It has no certificate. The names for JPM, BAC, and WFC match the
subsidiary names on the parent-to-child links in
`evals/golden/graph_snapshot.json`. The name for C is the BankFind legal
name `Citibank, National Association`. The graph abbreviation
`CITIBANK, N.A.` returned zero active rows on 2026-10-23. Production
extract does not read that file and does not read `fdic_cert`.

The command looks up each legal name with an exact `NAME` filter and
`ACTIVE:1`. The certificate is the one active row whose name matches after
case-folding and whitespace collapse. Zero matches or several matches exit 1.
Companyfacts and one financials page follow. The financials request uses
`limit=10000` and does not page. `meta.total` must equal the row count and
be at least 1.

The command holds every body, trims them, then writes. A failure writes
nothing. `sha256` and `bytes` describe the trimmed body. `url` stays the
source URL. A second run replaces the files it names and rewrites
`raw/metrics/lead_banks.json` in request order. That file is ticker and
certificate only. A certificate file the new link no longer names stays on
disk.

Companyfacts trim keeps `cik`, `entityName`, and `facts`, drops concept
labels and descriptions, and keeps observations whose `val` is a finite
number. Observation keys are `start`, `end`, `val`, `accn`, `fy`, `fp`,
`form`, and `filed`. Financials trim keeps `CERT`, `REPDTE`, `ASSET`, `DEP`,
`NIMY`, `EEFFR`, and `NETINC`. A missing number is null. Bool, NaN, and
Infinity are rejected. An integral float is stored as an int. `CERT` is an
int. `REPDTE` is an eight-digit string. Trim is idempotent, so the committed
fixtures are fixed points of the same projection a live run writes.

`ASSET`, `DEP`, and `NETINC` are thousands of USD. `NIMY` and `EEFFR` are
percent. SEC companyfacts values in USD units are whole USD.

`NETINC` is year-to-date cumulative net income. FDIC BankFind Suite
methodologies say bank Call Reports disclose income and expenses on a
year-to-date basis, and that a quarter is derived by subtracting the prior
Call Report. Source:
https://banks.data.fdic.gov/bankfind-suite/help?helpTopic=disclaimers-and-methodologies
A later dbt modeling step will de-cumulate. This record does not do that.

Exit 1 is a bad flag, an unknown ticker, a dishonest fixture, a name that is
not one active exact match, a truncated page, or a body for the wrong
entity. Exit 2 is `HttpTransportError` or `HttpStatusError`, including a 301.
`--fixtures` looks up `tests/fixtures/metrics` from the repository root
that contains `pyproject.toml`, not from the process working directory, and
only when that flag is set. Importing the CLI does not require that tree.
A packaged install without a checkout still runs `--help`. A missing tree
on `--fixtures` is exit 1. There is no `--fixtures-dir`.

## Image console scripts

The runtime image already copied the project venv to `/app/.venv`. The
builder used to write that venv at `/src/.venv`, so `capitalcite` and
`uvicorn` shebangs still named `/src/.venv/bin/python`. The copied script
existed. The interpreter path did not. Infra only ran `docker build`, so
the broken scripts were already in the image. That failure is older than
the packaged `--help` import crash. `UV_PYTHON_DOWNLOADS=0` keeps the
builder on the image Python. The builder now sets
`UV_PROJECT_ENVIRONMENT` to `/app/.venv` so shebangs name
`/app/.venv/bin/python`, which is the same interpreter the runtime PATH
uses. Changing the image entrypoint to `python -m` would hide the next
broken console script. Infra runs `capitalcite --help` and
`python -c "import capitalcite.api"` in the built image.

## What BankFind and EDGAR returned on 2026-10-23

Exact `NAME` lookup returned one active institution for three holdings.

JPM matched `JPMORGAN CHASE BANK, NATIONAL ASSOCIATION` at certificate 628.
BAC matched `BANK OF AMERICA, NATIONAL ASSOCIATION` at 3510. WFC matched
`WELLS FARGO BANK, NATIONAL ASSOCIATION` at 3511.

`CITIBANK, N.A.` returned zero rows. A query for
`Citibank, National Association` returned certificate 7213. The holdings
table stores that BankFind name. A four-ticker live extract on 2026-10-23
exited 0 and printed:

```text
lead ticker=JPM cert=628
xbrl ticker=JPM bytes=7319235 sha256=ce6f78514efff0b86de7f1e08942dc7cb8944f318a90bdcded0ed85a43396dfd
financials cert=628 rows=170 bytes=24396 sha256=10212f3e39209da5f7cd7607cf6710a115f849d9e2fd189578f64e8d1e8ad724
lead ticker=BAC cert=3510
xbrl ticker=BAC bytes=6285851 sha256=1f8b615b34a0e9cfadd2899f6a3722897c88e5953127e854a360eed9f6d7ebb9
financials cert=3510 rows=170 bytes=24615 sha256=f537664564a64a9082f272c7e91cc3e597df91f0076a9f9add1b1530f1f77690
lead ticker=WFC cert=3511
xbrl ticker=WFC bytes=7126412 sha256=1cf414ac020eb8ee72d5870796c2ee4fe1eebdd31b15c4da250b011dd44ad934
financials cert=3511 rows=170 bytes=24437 sha256=1080d7c25279b46e1ee2acff95a22f4636f77a36dfffd310b9e0d55525be8982
lead ticker=C cert=7213
xbrl ticker=C bytes=8368398 sha256=9d0035d4c4b7e43015584d9d3b5336d6a14d64e03744cc75afa90ffd0de94e3c
financials cert=7213 rows=170 bytes=24601 sha256=ae755da8a0e81c6eb2c8e2daa23fa52c014b0ffbecf7e25ce059aac74efb45c2
metrics extract xbrl=4 financials=4 output=/tmp/metrics-live-four
```

Those row counts are what BankFind returned. They are not a pass or fail
check. The financials sha256 values matched the committed fixture files.
Committed financials files are the trim of those pages. Committed
companyfacts files keep two `Assets` observations so the git objects stay
small. SEC companyfacts for CIK `0000070858` named the entity
`BofA Finance LLC`.
