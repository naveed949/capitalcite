# 0018. Eval report selection and the fake-model default

Date: 2026-10-17

Status: Accepted

Issue #17's write-up may land `0017` in parallel. That number is not on
`main` at `997aac8`, so this record is `0018`.

This record is why `load_reports` no longer returns every valid JSON in
filename order, why stale `evals/results/*.json` files stay in the tree,
and why `ASK_MODEL` defaults to `fake`.

## Last filename is not a published matrix

`capitalcite eval --from-results` loads stored `EvalReport` JSON and can
rewrite the README retrieval table. ADR 0016 already said those cells come
from `evals/results/<sha12>.json` written in the same run, not from an older
SHA. The Makefile still passed the whole `evals/results/` directory.

`load_reports` parsed every `*.json`, skipped graph and sidecar files that
fail `EvalReport` validation, and returned the rest in sorted filename
order. The CLI wrote the README once per report. The last name won.
`aceba4024177.json` sorts after `7388ac3eea43.json`. On `main` that rewrite
replaced section/BM25 Recall@5 `0.182` with `0.000` from a commit that is
not an ancestor of HEAD.

The recorded `git_sha` inside each file is the identity. A directory pick
uses that field, not the path.

1. A single file is that file.
2. One valid report in a directory is that report.
3. If any report's `git_sha` matches `git rev-parse HEAD` (full or prefix),
   keep those matches.
4. Else keep reports whose `git_sha` is an ancestor of HEAD, and take the
   newest commit time.
5. Prefer `small=false` when both the full matrix and `*-small.json` remain.
6. If more than one report still remains, raise. Name the files. Tell the
   caller to pass one file or `RESULTS=evals/results/<sha12>.json`.

`make eval-report` sets `RESULTS` to `evals/results/$(git rev-parse --short=12 HEAD).json`
when that file exists. Otherwise it passes the directory and the rule
above selects. Override with `RESULTS=`. A missing `RESULTS` path exits 1.

## Keep the historical JSON

`tests/test_check.py` pins `8b63d12acd50.json` as the published nightly
snapshot. ADR 0016 left that file in the tree on purpose. `4d472e1c3922.json`
and `aceba4024177.json` are earlier matrices. Deleting them would hide the
filename-order bug and break the pin. Selection ignores them. It does not
delete them.

## Fake is the no-key default

ADR 0007 set `ASK_MODEL` to `openai` so a server without `OPENAI_API_KEY`
returned HTTP 503 instead of quoting a chunk. That is the realistic default.
It is also the one that fails the README quickstart until someone edits
`.env`.

The documented local path is what a reviewer runs first. `.env.example`,
`Settings.ask_model`, and the Docker image now default to `fake`. A
commented `# ASK_MODEL=openai` line shows the switch. Explicit `openai`
still requires a key. `build_structured_model("openai", api_key=None)` still
raises `ModelNotConfigured`. Nothing maps `openai` to `fake` when the key
is empty.

`cp .env.example .env` used to leave `ASK_MODEL=openai`, so the first
`/ask` without a key returned 503. `fake` quotes a retrieved chunk and
costs nothing. Realism is one uncommented line.
