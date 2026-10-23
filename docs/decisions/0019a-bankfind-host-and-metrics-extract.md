# 0019a. BankFind host and metrics extract

Date: 2026-10-23

Status: Accepted

`0019` and `0020` are unused. This record is `0019a`.

Point institutions lookup at `api.fdic.gov/banks` and keep a 301 fail-closed.
`capitalcite metrics extract` stores holding-company companyfacts and
lead-bank call reports as separate raw snapshots. Certificate numbers come
from a live name lookup. `--fixtures` copies committed bytes and does not
open a socket.

## Keep a 301 fail-closed

`HttpxJsonClient` does not set `follow_redirects`. A replayed 301 raises
`ValueError` matching `HTTP 301` after one request. The metrics command
maps that error to exit 2. Transport failures still surface as
`request failed`.

Institutions are `https://api.fdic.gov/banks/institutions`. Financials are
`https://api.fdic.gov/banks/financials`. The ask route still builds one SEC
client with `shared_limiter` and one FDIC client without that limiter. Both
send `SEC_USER_AGENT`. `ASK_MODEL=fake` still does not call either host.

## Write two series from one command

A holding company has a ticker, a 10-digit CIK, and the lead bank's legal
name. It has no certificate. Those legal names are the subsidiary names on
the parent-to-child links in `evals/golden/graph_snapshot.json`. Production
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

Exit 1 is a bad flag, an unknown ticker, a dishonest fixture, a name that is
not one active exact match, a truncated page, or a body for the wrong
entity. Exit 2 is a transport failure or any non-200, including a 301.
`--fixtures` reads `tests/fixtures/metrics`. There is no `--fixtures-dir`.

## What BankFind and EDGAR returned on 2026-10-23

Exact `NAME` lookup returned one active institution for three holdings.

JPM matched `JPMORGAN CHASE BANK, NATIONAL ASSOCIATION` at certificate 628.
BAC matched `BANK OF AMERICA, NATIONAL ASSOCIATION` at 3510. WFC matched
`WELLS FARGO BANK, NATIONAL ASSOCIATION` at 3511.

`CITIBANK, N.A.` returned zero rows. A separate query for
`Citibank, National Association` returned certificate 7213. The holdings
table keeps the graph subsidiary name, so this command exits 1 and creates
no output directory:

```text
capitalcite metrics extract --tickers JPM,BAC,WFC,C --output-dir /tmp/metrics-live-four
error: lead bank name for C did not match one active institution
```

The same command without `C` exited 0. It printed `rows=170` for
certificates 628, 3510, and 3511. Trimmed companyfacts were 7319235 bytes
for JPM, 6285851 bytes for BAC, and 7126412 bytes for WFC. The financials
sha256 values matched the committed fixture files for those three
certificates. Financials for certificate 7213, fetched directly in the same
session, also had 170 rows. The fixture manifest records 7213 for `C`. The
four-ticker command did not write that file.

Those row counts are what BankFind returned. They are not a pass or fail
check. Committed financials files are the trim of those pages. Committed
companyfacts files keep two `Assets` observations so the git objects stay
small. SEC companyfacts for CIK `0000070858` named the entity
`BofA Finance LLC`.

A later dbt modeling step can read this layout. This record does not add
that step.
