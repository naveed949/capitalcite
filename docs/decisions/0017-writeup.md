# 0017. Write-up numbers are checked, not generated

Date: 2026-10-17

Status: Accepted

Issue #17 asks for a project write-up with real metrics and a
failure analysis. This record is why that file is authored prose,
why the lever is a checker rather than `eval --readme`, and why a
cited number is the written lexeme. Every number a reader sees must
trace to a committed result.

## The write-up is selected prose

README tables are already generated from
`evals/results/<sha12>.json` (ADR 0006, ADR 0016). A project
write-up is not a table. It has a voice, a first-person argument,
and an honest miss analysis that names item ids. Extending
`capitalcite eval --readme` to this file would force a template
language onto sentences it is bad at writing.

`docs/writeup.md` sits beside `docs/DECISIONS.md`. It is not a
record. Putting it under `docs/decisions/` would look like an ADR.

## A cited number is the token as written

The write-up may use a lexeme when `README.md` contains that same
lexeme, or a short projection from a results JSON file that both
documents link. The projection emits an
arm's `hits/items` ratio when `f"{hits/items:.3f}"` is already a
README cell, plus counts of empty `covered_at` and of first ranks
from 6 through 10. It does not walk every JSON integer.
`point_count`, raw floats, diagnose.md ranks, and `*-unverified.json`
stay out.

The key is the slice, not `float(token)`. `0.169` is not
`0.077922...`. `5/5` is atomic. `12-point` is not `12`. `14 ms` is
not `14`. Cost cells that display `[0]` and point at
`*-unverified.json` do not publish a measured zero. A line that says
`cost` next to `0` fails.

Exclusions live in `scripts/check_writeup_numbers.py`. SHAs, ISO
dates, `#17`, ADR ids, URL paths, and `L79` locators are not claims.
Authors do not annotate them.

## The hook is cheap and fail-closed

`uv run python scripts/check_writeup_numbers.py` exits 0 or 1. It
never exits 2. `verify.sh` treats 2 as skip. The check runs in
`.github/workflows/ci.yml` job `check` and as an early `verify.sh`
step after `uv sync --frozen`. It is not folded into
`capitalcite check` or `eval --small`. Those commands score retrieval
and contracts. `--small` Recall@5 is 0.000 on fixture HTML and cannot
catch a invented README cell.

A docs-only PR still runs lint, pytest, `capitalcite check`, and
`eval --small`. It does not run the twelve-filing nightly embed.
Markdown is not on that workflow's path filter.

The checker is necessary, not sufficient. Homographs remain. README
`64` is chunk overlap, so a write-up that says `64` will pass even
when the sentence meant misses. Review still reads the sentence.

## What this does not change

No new Python API. No paid provider. No Azure apply. No change to
retrieval, eval scoring, or gate policy. `eval --readme` still owns
the README tables.
