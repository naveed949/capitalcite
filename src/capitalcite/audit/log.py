"""Append-only JSONL hash chain. AuditLog is the only writer."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from capitalcite.audit.digest import digest, sha256_json
from capitalcite.audit.types import (
    GENESIS_HASH,
    AuditRecord,
    Decision,
    VerifyIssue,
    VerifyReport,
)


def utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class AuditLog:
    """One JSONL file. `record` appends one chained line. `verify` walks the file."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path
        self._clock = clock or (lambda: datetime.now(UTC))

    def record(
        self,
        *,
        trace_id: str,
        tool: str,
        args: object,
        decision: Decision,
        output: object,
        reason: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditRecord:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        if not tool.strip():
            raise ValueError("tool must not be blank")
        ts = utc_iso(timestamp if timestamp is not None else self._clock())
        args_digest = digest(args)
        output_digest = digest(output)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                prev_hash, seq = _tail(handle)
                chained = AuditRecord(
                    seq=seq + 1,
                    ts=ts,
                    trace_id=trace_id.strip(),
                    tool=tool.strip(),
                    args_digest=args_digest,
                    decision=decision,
                    reason=reason,
                    output_digest=output_digest,
                    prev_hash=prev_hash,
                    hash=sha256_json(
                        {
                            "seq": seq + 1,
                            "ts": ts,
                            "trace_id": trace_id.strip(),
                            "tool": tool.strip(),
                            "args_digest": args_digest,
                            "decision": decision,
                            "reason": reason,
                            "output_digest": output_digest,
                            "prev_hash": prev_hash,
                        }
                    ),
                )
                handle.write(chained.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return chained


def verify(path: Path) -> VerifyReport:
    """Walk the chain. An empty file is valid. A missing file is not."""
    if not path.is_file():
        return VerifyReport(
            ok=False,
            records=0,
            issues=(VerifyIssue(seq=None, reason=f"missing audit log: {path}"),),
        )
    issues: list[VerifyIssue] = []
    prev_hash = GENESIS_HASH
    expected_seq = 1
    records = 0
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            issues.append(VerifyIssue(seq=None, reason=f"blank line at {path}:{line_no}"))
            continue
        try:
            record = AuditRecord.model_validate_json(raw)
        except ValidationError as exc:
            issues.append(
                VerifyIssue(seq=None, reason=f"invalid record at {path}:{line_no}: {exc}")
            )
            continue
        records += 1
        payload = record.model_dump(mode="json", exclude={"hash"})
        expected_hash = sha256_json(payload)
        if record.hash != expected_hash:
            issues.append(
                VerifyIssue(seq=record.seq, reason=f"hash mismatch at seq {record.seq}"),
            )
        if record.prev_hash != prev_hash:
            issues.append(
                VerifyIssue(
                    seq=record.seq,
                    reason=f"prev_hash mismatch at seq {record.seq}",
                ),
            )
        if record.seq != expected_seq:
            issues.append(
                VerifyIssue(
                    seq=record.seq,
                    reason=f"seq gap: expected {expected_seq}, got {record.seq}",
                ),
            )
        prev_hash = record.hash
        expected_seq = record.seq + 1
    return VerifyReport(ok=not issues, records=records, issues=tuple(issues))


def _tail(handle: TextIO) -> tuple[str, int]:
    handle.seek(0)
    last: AuditRecord | None = None
    for raw in handle.read().splitlines():
        if not raw.strip():
            continue
        last = AuditRecord.model_validate_json(raw)
    if last is None:
        return GENESIS_HASH, 0
    return last.hash, last.seq
