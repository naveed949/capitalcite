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
A deleted line fails `prev_hash` or `seq`. An inserted line fails both.
A missing file fails. An empty file is a valid unused log (`records=0`).
Args and outputs are stored as digests, not payloads, because the issue
asked for digests and because a filing quote does not belong in an
ops log.

The write is one full JSON line plus a newline, then `fsync`, under an
exclusive flock. `atomic_write_*` replaces a whole file. Replacing the
log on every call would lose history if the process died between
truncate and rename. Append is the operation that matches append-only.

## JSONL, not the Langfuse Postgres

The issue allowed a table. The app still has no database. Langfuse's
Postgres is an observability product's store. Putting the legal audit
trail there couples tamper evidence to a compose service tests do not
run. Dual JSONL/Postgres backends would be two ways to do one task.

A hash-chained JSONL file lives under `AUDIT_LOG_PATH`
(default `data/audit.jsonl`, gitignored). Pytest and
`capitalcite audit verify` need no Docker. If deploy later requires
SQL, the public surface stays `record` and `verify`. The file is an
implementation.

## One writer, a wrapper, not execute()

`ToolRegistry.execute` is the choke point for tool names, but it is not
the gate. `ok` is tool success. An unknown name is the only deny today
(`detail == "unknown tool"`). A calculator overflow is an allowed call
that failed. Issue #9 will wrap `execute` with a policy. If #10 wrote
inside `execute`, a later gate that records its own decision would
double-write every allow.

`AuditLog.record` does not know tools. `AuditingTools.execute` calls
the inner registry, maps unknown-tool to `denied` and every dispatched
call to `allowed`, writes one line, and returns the `ToolResult`.
`run_agent` types that as `ToolExecutor`. `_run_agent` mints a UUID
`trace_id` for the request and wraps the default registry. Issue #9
replaces `AuditingTools` with the gate. The gate calls `record` itself
and must not also wrap with `AuditingTools`. Issue #11 replaces the
UUID with a Langfuse trace id. The field name stays `trace_id`.

Budget skips never reach `execute`. They are not gated calls and they
do not write.

## What this leaves out

The fail-closed policy file is issue #9. Langfuse spans are issue #11.
This change does not import those SDKs. Concurrent multi-process
writers share one flock. That is enough for one API process. It is not
a distributed ledger. Live `path=agent` with `model=openai` stays
UNVERIFIED without `OPENAI_API_KEY`.
