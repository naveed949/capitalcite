# 0013. Behavioral regression tests in CI

Date: 2026-10-16

Status: Accepted

Issue #12 asks for contract tests (always cites, refuses unanswerable,
never calls tools outside the policy) plus an eval-subset regression
gate. A GitHub Action must run a fast subset on PRs and the full set
nightly. A PR fails when a tracked metric drops more than a configured
threshold below a committed baseline. Updating that baseline requires
the `eval-baseline` label.

## Two kinds of tests

A unit test pins an internal function. A behavioral test pins what a
user of the product can observe. `capitalcite check` is the second
kind. It drives the same CLI, FakeEmbedder, fake model, and gate
red-team that a reviewer can rerun. Pytest still covers `compare()`
and CLI flags. Pytest is not the gate.

Cite, refuse, and deny are pass/fail. One miss is a product failure.
Retrieval rates are not. ADR 0006 already said n=77 and one item is
0.013 recall. A 1% relative drop is meaningless at that n, and it is
worse at n=3. The gate counts item hits. It fails when
`committed.hits - current.hits` is greater than `max_drop_items`.

## What PRs actually gate

`capitalcite eval --suite golden --small` scores the 77 handwritten
12-K gold items against the two-file fixture corpus. Span overlap
never hits. Every Recall@k cell is 0.000. Gating that JSON cannot
fail on a retrieval-quality drop. The command stays as a smoke test
that `n_items`, arm count, and `embedder_id` stay
`fake:deterministic`.

The retrieval numbers that can drop are:

- Quote-index sanity. Each gold quote is its own chunk. FakeEmbedder
  hybrid Recall@5 is lexical overlap, not corpus recall. It is fast
  and non-zero.
- Fixture span-overlap on `evals/golden/ci.jsonl`. Those three items
  were written against the fixture HTML. They are not part of the
  published 112-item v1 set. Putting them in `v1.jsonl` would change
  the mix counts and the README table.

Contracts on that same fixture index:

- `cc-ci-002` must come back `answered` with grounded citations
  (`ASK_MODEL=fake` quotes a hit).
- `cc-u-001` (Wells Fargo, year-end 2026) must come back `refused`
  because the fixture hits are JPM FY2024.

`capitalcite gate redteam` must report `failed=0`. That suite is
already the user path for "never calls tools outside the policy."

## Noise, flake, and cost

FakeEmbedder and the fake chat model are deterministic. There is no
LLM sampling in PR CI. The item budget (`quote_sanity_max_drop_items=1`,
`fixture_max_drop_items=1`) is the small-n cushion so a single
rank-boundary flip does not fail the build. Two missed quote-sanity
hits, or two missed fixture items, fail.

Live OpenAI is `--live`. Empty `OPENAI_API_KEY` is UNVERIFIED, not a
failure. The nightly job skips the same way. PR CI does not pass
`--live`. That is cost control and flake control in one flag.

The twelve-filing bge-small matrix needs `data/raw/edgar`. That tree
is gitignored. Nightly full eval exits 0 with UNVERIFIED when the
corpus is missing. CI does not ingest EDGAR.

## Baseline updates

`evals/baselines/ci.json` is the snapshot. `capitalcite check`
compares the current run to the file in the checkout. Lowering the
file would pass the compare. The `eval-baseline` label is the
control. A workflow job diffs `evals/baselines/` against the PR base
and fails when those files changed and the label is absent.

`capitalcite check --write-baseline` rewrites the file from a local
run. CI never passes that flag.

## Alternatives

Putting the gate only in GitHub Actions YAML would hide the compare
from a laptop. The CLI is the lever. YAML calls it.

Gating `--small` Recall@5 would look like a retrieval gate and would
never fire.

A relative 5% drop would fail or pass at random at n=3.

RAGAS and DeepEval need an LLM judge. Those rows stay UNVERIFIED.
