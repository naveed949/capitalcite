# Audit log

`capitalcite audit verify` walks the hash-chained JSONL of gated tool calls
and exits 0 only when every record's hash and `prev_hash` chain hold and
the sibling tip matches the last line. A mutated field, a deleted middle
line, a deleted last line, or an inserted line exits 1. The command is
offline. It does not call EDGAR, Qdrant, or an LLM.

## Sub-features

- `audit-help` lists `verify` and `--path`.
- `audit-ok` exits 0 on an intact chain and prints `audit ok records=N`.
- `audit-tamper` exits 1 after a field is mutated.
- `audit-removed` exits 1 after the last line is deleted (tip still present).
- `audit-gated-calls` counts one JSONL record per agent `traces[]` entry after `POST /ask` `path=agent`, and each record's `trace_id` matches the `/ask` root span in `spans.jsonl`. UNVERIFIED when the agent path could not run.

## How to get to it (user POV)

- Run `uv run capitalcite audit verify`.
- Override the file with `--path` (defaults to `data/audit.jsonl`).
- Agent requests write that file when `AUDIT_LOG_PATH` is set (default `data/audit.jsonl`).

## Driving it with the capitalcite CLI

Preconditions:

- `uv sync --frozen` has been run in this checkout.
- Doctor: `uv run capitalcite audit verify --help` exits 0.
- Tamper detection does not need uvicorn, Docker, or live EDGAR.
- The one-record-per-gated-call check needs the `POST /ask` `path=agent` step on this run. If that step was UNVERIFIED, this check is UNVERIFIED.

- **Show help.** Run `uv run capitalcite audit verify --help`. Exit code is `0`. Stdout mentions `--path`.
- **Verify an intact chain.** Write two records through `AuditingTools` (or run `path=agent`), then run `uv run capitalcite audit verify --path $LOG`. Exit code is `0`. Stdout includes `audit ok records=` with a positive count.
- **Detect tamper.** Copy that file and its sibling `.tip`, change one tool name in a line, run verify again. Exit code is `1`. Stderr includes `audit fail`.
- **Detect a removed last record.** Copy the intact file and its `.tip`, drop the last JSONL line, run verify again. Exit code is `1`. Stderr includes `audit fail` and a tip mismatch.
- **Count gated calls.** After `POST /ask` `path=agent`, the number of JSONL records for that `trace_id` equals `len(traces)` in the response. Every record's `trace_id` equals the `ask` root span `trace_id` in `spans.jsonl`.
- **Bundled helper.** Run `./scripts/verify.sh`. It writes `audit.help.out`, `audit.ok.out`, `audit.tamper.out`, `audit.removed.out`, and either `audit.agent.out` or `audit.agent.skipped`.
- **Proof.** Keep the CLI stdout, stderr, and exit codes, plus the JSONL file. Pytest is not this feature.

## Gotchas

- `CliRunner().invoke(...)` in pytest is not this feature.
- An empty file with no sibling `.tip` is valid (`records=0`). A missing file is a failure.
- Relocating the JSONL without its sibling `<log>.tip` fails verify on a non-empty log.
- `path=rag` does not write the log. Only gated tool calls do.
- Do not start Docker to verify the chain. The file is local JSONL.
- `trace_id` is `current_trace_id()` from the `/ask` root span. A UUID appears only when tracing is off.
- The wrapper writes the gate decision before the tool runs. A failed write is `kind=refused`, `refusal_reason=audit unavailable`, not HTTP 500.
- Deleting both the JSONL and the sibling `.tip` verifies as an empty unused log. The chain is not HMAC-keyed.
