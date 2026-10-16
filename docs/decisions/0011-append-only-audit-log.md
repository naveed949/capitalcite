# 0011. Append-only audit log

Date: 2026-10-16

Status: Accepted

Issue #10 asks for a tamper-evident log of every gated tool call:
timestamp, trace id, tool, args digest, decision, and output digest.
`capitalcite audit verify` has to catch a mutated or deleted line. The
issue allows hash-chained JSONL or a Postgres table. This record is why
the log is a JSONL hash chain, why `AuditLog.record` is the only writer,
and why the agent wraps `execute` instead of teaching `ToolRegistry`
about files.

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

## One writer, a wrapper around the gate

ADR 0009 put `Gate` on `execute`. `ok` is tool success. A deny is
`ToolResult.reason` set to a `DenyReason` (`unknown_tool`,
`invalid_args`, `args_too_large`, `budget_exceeded`,
`policy_load_failed`, `gate_error`). A calculator overflow is an
allowed call that failed (`reason` is null).

`AuditLog.record` does not know tools. `AuditingTools.execute` wraps
`Gate`, copies `reason` onto the JSONL line, maps a set reason to
`denied` and a missing reason to `allowed`, and returns the
`ToolResult`. Gate does not write the log. `_run_agent` constructs
`AuditingTools(Gate(policy, registry), log, trace_id)` once per
request. `trace_id` is a UUID until #11 supplies a Langfuse id.

Budget skips never reach `execute`. They are not gated calls and they
do not write.

## What this leaves out

Langfuse spans are issue #11. This change does not import that SDK.
Concurrent multi-process writers share one flock. That is enough for
one API process. It is not a distributed ledger. Live `path=agent`
with `model=openai` stays UNVERIFIED without `OPENAI_API_KEY`.
