# Behavioral regression check

`capitalcite check` runs the PR regression pack against
`evals/baselines/ci.json`. It scores quote-index sanity and fixture
span-overlap, drives fake-model cite/refuse contracts, and runs
`gate redteam`. The committed PR budget is zero item drops. `--live` without
`OPENAI_API_KEY` prints UNVERIFIED and still exits 0 when the
deterministic pack passed. The twelve-filing compare is
`capitalcite eval --baseline`, not this command.

## Sub-features

- `check-help` lists `--baseline`, `--live`, `--write-baseline`, and `--out`.
- `check-pack` exits 0, prints `ok=true`, and writes `check.json` with quote_sanity, fixture, redteam_failed=0, and both contracts ok.
- `check-live` with an empty key prints `unverified:` and `OPENAI_API_KEY is empty`. That is UNVERIFIED live generation, not a product failure.

## How to get to it (user POV)

- Run `uv run capitalcite check` from the repo root.
- Rewrite the snapshot with `uv run capitalcite check --write-baseline` only when the PR will carry the `eval-baseline` label.

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- `evals/baselines/ci.json` and `evals/golden/ci.jsonl` are present.
- This path does not need uvicorn, Docker, live EDGAR, or `OPENAI_API_KEY`.
- Doctor: `uv run capitalcite check --help` exits 0.

- **Show help.** Run `uv run capitalcite check --help`. Exit code is `0`. Stdout mentions `--baseline` and `--live`.
- **Run the pack.** Run `uv run capitalcite check --out $ART/check`. Exit code is `0`. Stdout includes `ok=true` and `redteam_failed=0`. `$ART/check/check.json` has `live.kind` `skipped`.
- **Live OpenAI.** Run `uv run capitalcite check --live --out $ART/check-live`. If `OPENAI_API_KEY` is empty, exit code is `0` and stdout or stderr includes `unverified` and `OPENAI_API_KEY`. Record that as UNVERIFIED, not a pass of live generation.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `check.help.out`, `check.out`, `check.exit`, and `check/check.json`, plus `check.live.unverified` when the key is missing.
- **Proof.** Keep the CLI stdout, exit code, and `check.json`. Pytest is not this feature.

## Gotchas

- `eval --small` Recall@5 on the 77-item gold set is 0.000 against the fixture corpus. That is not the gated retrieval number. Quote-index sanity and `evals/golden/ci.jsonl` are.
- The PR pack is deterministic, so `quote_sanity_max_drop_items` and `fixture_max_drop_items` are 0. The 1-item allowance is nightly only.
- `--write-baseline` rewrites the snapshot. CI never passes that flag. Changing `evals/baselines/` on a PR without the `eval-baseline` label fails a workflow job.
- `CliRunner().invoke(...)` in pytest is not this feature.
