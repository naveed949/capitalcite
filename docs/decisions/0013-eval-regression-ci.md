# 0013. Behavioral regression tests in CI

Date: 2026-10-16

Status: Accepted

Issue #12 asks for contract tests (always cites, refuses unanswerable,
never calls tools outside the policy) plus an eval-subset regression
gate. A GitHub Action must run a fast subset on PRs and the full set
nightly. A PR fails when a tracked metric drops more than a configured
threshold below a committed baseline. Updating that baseline requires
the `eval-baseline` label. Azure Container Apps took ADR 0012, so this
record is 0013.

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
LLM sampling in PR CI. The PR pack therefore uses
`quote_sanity_max_drop_items=0` and `fixture_max_drop_items=0`. A
one-item miss is a product change, not rank-boundary noise. The
1-item allowance lives on the nightly twelve-filing matrix instead.

Live OpenAI is `--live`. Empty `OPENAI_API_KEY` is UNVERIFIED, not a
failure. The nightly live job skips the same way. PR CI does not pass
`--live`. That is cost control and flake control in one flag.

## Nightly twelve-filing matrix

`data/raw/edgar` is gitignored. A job that skipped when that tree was
missing would never score the golden set on GitHub-hosted runners.
Nightly therefore ingests the twelve 10-Ks itself
(`JPM,BAC,WFC,C` × `10-K` × `2023-2025`) with
`SEC_USER_AGENT=capitalcite-ci ci@example.com`. EDGAR is public. The
filings cache under `actions/cache` keyed on that filing list.
FastEmbed models cache under `FASTEMBED_CACHE_PATH`. The eval index
caches under `QDRANT_PATH`; `build_index` skips re-embed when point
ids already match. Caches save after ingest, model warmup, and a
successful eval. `capitalcite eval --suite golden` re-chunks, indexes
embedded Qdrant with `bge-small` (all-core FastEmbed passage embed),
and compares Recall@5 to `evals/baselines/nightly.json`. Ingest is
bounded by five EDGAR retries and a 10-minute step timeout. The job
timeout is 90 minutes; the eval step is 50.

UNVERIFIED (exit 0) is only ingest exit 2: `EdgarAccessError` or
`EdgarUnavailableError` (403, 429 after retries, 5xx, transport). A
short corpus after any other ingest failure fails the job. Eval
itself is not skipped.

Nightly `max_drop_items` is 1. The committed matrix is n=77 against
live bge-small ranks. One item moving across the cutoff is noise. Two
is a drop. `agent_vs_rag` is not gated; that row is a planner probe,
not a retrieval baseline.

`capitalcite eval --baseline` is the compare. `--small` rejects that
flag. CI never passes `--write-baseline`.

## Baseline updates

`evals/baselines/ci.json` is the PR snapshot.
`evals/baselines/nightly.json` is the twelve-filing snapshot.
Lowering either file would pass the compare. The `eval-baseline`
label is the control. A workflow job diffs `evals/baselines/` against
the PR base and fails when those files changed and the label is
absent.

`capitalcite check --write-baseline` and
`capitalcite eval --write-baseline` rewrite those files from a local
run. CI never passes those flags.

## Alternatives

Putting the gate only in GitHub Actions YAML would hide the compare
from a laptop. The CLI is the lever. YAML calls it.

Gating `--small` Recall@5 would look like a retrieval gate and would
never fire.

A relative 5% drop would fail or pass at random at n=3.

A 1-item PR budget would hide a deterministic FakeEmbedder miss.

Skipping nightly when `data/` is missing would make the full golden
eval a no-op on every hosted runner.

RAGAS and DeepEval need an LLM judge. Those rows stay UNVERIFIED.
