# 0011. Append-only audit log

Date: 2026-10-16

Status: Accepted

Issue #10 asks for a tamper-evident log of every gated tool call:
timestamp, trace id, tool, args digest, decision, and output digest.
`capitalcite audit verify` has to catch a mutated or deleted line. The
issue allows hash-chained JSONL or a Postgres table. This record is why
the log is a JSONL hash chain, why `AuditLog.record` is the only writer,
why the wrapper writes before the tool runs, and why `trace_id` is
`current_trace_id()`.

## A chain, not a pile of lines

An audit log that cannot detect a missing line is a diary. Banks need
the other shape. Each record stores `prev_hash` of the previous record's
`hash`. Genesis `prev_hash` is 64 zero hex characters. `hash` is SHA-256
of the canonical JSON of every field except `hash` itself. Canonical
JSON is sorted keys and compact separators, the same rule
`retrieval.hashing.config_hash` already uses for index config.

`verify` walks the file and recomputes. A changed field fails the hash.
A deleted middle line fails `prev_hash` or `seq`. An inserted line fails
both. A missing file fails. An empty file with no sibling tip is a valid
unused log (`records=0`). Args and outputs are stored as digests, not
payloads, because the issue asked for digests and because a filing quote
does not belong in an ops log.

A deleted last line does not break the remaining chain. The leftover
prefix is a valid shorter log. A sibling `<log>.tip` stores the last
`seq` and `hash` as canonical JSON. `verify` fails when a non-empty log
has no tip, when the tip's seq or hash disagrees with the last line, or
when an empty log still has a tip. Copying the JSONL without the tip
fails for the same reason.

The tip is also the write-path cache for `prev_hash`. Happy-path
`record` reads the tip instead of scanning the JSONL. It scans only when
the tip is missing. That keeps append O(1) in the size of the log.

The write is one full JSON line plus a newline, then `fsync`, under an
exclusive flock, then `atomic_write_*` of the tip while still holding
the lock. `atomic_write_*` replaces a whole file. Replacing the log on
every call would lose history if the process died between truncate and
rename. Append is the operation that matches append-only. The tip is a
one-record witness, so replace is the right write for that file.

## JSONL, not the Langfuse Postgres

The issue allowed a table. The app still has no database. Langfuse's
Postgres is an observability product's store. Putting the legal audit
trail there couples tamper evidence to a compose service tests do not
run. Dual JSONL/Postgres backends would be two ways to do one task.

A hash-chained JSONL file lives under `AUDIT_LOG_PATH`
(default `data/audit.jsonl`, gitignored) with a sibling
`audit.jsonl.tip`. Pytest and `capitalcite audit verify` need no Docker.
If deploy later requires SQL, the public surface stays `record` and
`verify`. The file is an implementation.

## Write before the tool, then maybe run it

ADR 0009 put `Gate` on `execute`. `ok` is tool success. A deny is
`ToolResult.reason` set to a `DenyReason` (`unknown_tool`,
`invalid_args`, `args_too_large`, `budget_exceeded`,
`policy_load_failed`, `gate_error`). A calculator overflow is an
allowed call that failed (`reason` is null). The gate always sets
`reason` on a deny, so the audit wrapper does not special-case
`detail == "unknown tool"`.

Recording after `execute` is the wrong order. The tool would run, then
`AuditLog.record` raising would become HTTP 500 with no line. Fail-closed
means the opposite: `AuditingTools` calls `Gate.decide` once, writes the
record, and only then calls `Gate.run`. `run` applies that Decision
without spending `max_calls` again. If the write raises, the wrapper
returns `ToolResult(reason="audit_unavailable", detail="audit unavailable")`
and does not call the runner. `act_node` turns that into
`Refused(refusal_reason="audit unavailable")` (HTTP 200, not 500).

The line is the gated decision, not the later tool payload.
`output_digest` hashes the gate `detail` (`allow`, or the deny string).
An append-only chain cannot revise a digest after the tool returns.

`AuditLog.record` does not know tools. `_run_agent` constructs
`AuditingTools(Gate(policy, registry), log, request_trace_id())` once
per request, inside the `/ask` root `observation()`. `request_trace_id()`
is `capitalcite.trace.current_trace_id()`, with a UUID only when that
returns null (tracing off or unsampled). Budget skips never reach
`execute`. They are not gated calls and they do not write.

## What the chain actually proves

The hash chain is evidence against silent edits by someone who cannot
rewrite both the JSONL and the tip as a new valid chain. It is not a
keyed MAC. Anyone with write access to the log directory can delete
both files, or rebuild genesis and a matching tip; `verify` then reports
an empty log or a fresh intact chain. Deleting `audit.jsonl` and
`audit.jsonl.tip` together is indistinguishable from "never used."

An optional HMAC over the canonical payload, keyed by an env secret the
API process can read and the log directory cannot mint, would close that
gap. This change does not add it. The file chain is what #10 asked for
and what `capitalcite audit verify` can check without extra secrets.

Concurrent multi-process writers share one flock. That is enough for
one API process. It is not a distributed ledger. Live `path=agent`
with `model=openai` stays UNVERIFIED without `OPENAI_API_KEY`.
