from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from capitalcite.atomic import atomic_write_text
from capitalcite.audit.digest import canonical_json, digest, sha256_json
from capitalcite.audit.types import (
    GENESIS_HASH,
    AuditRecord,
    Decision,
    VerifyIssue,
    VerifyReport,
)

_HASH = re.compile(r"^[0-9a-f]{64}$")


def utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _tip_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tip")


def _write_tip(path: Path, *, seq: int, record_hash: str) -> None:
    atomic_write_text(path, canonical_json({"hash": record_hash, "seq": seq}) + "\n")


def _check_tip(path: Path, *, records: int, last_hash: str, last_seq: int) -> list[VerifyIssue]:
    tip_path = _tip_path(path)
    if records == 0:
        if tip_path.is_file():
            return [
                VerifyIssue(seq=None, reason=f"tip present for empty log: {tip_path}"),
            ]
        return []
    if not tip_path.is_file():
        return [VerifyIssue(seq=last_seq, reason=f"missing tip: {tip_path}")]
    try:
        raw = json.loads(tip_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [VerifyIssue(seq=last_seq, reason=f"invalid tip: {exc}")]
    if not isinstance(raw, dict):
        return [VerifyIssue(seq=last_seq, reason="invalid tip: not an object")]
    issues: list[VerifyIssue] = []
    if raw.get("hash") != last_hash:
        issues.append(
            VerifyIssue(seq=last_seq, reason=f"tip hash mismatch at seq {last_seq}"),
        )
    if raw.get("seq") != last_seq:
        issues.append(
            VerifyIssue(
                seq=last_seq,
                reason=f"tip seq mismatch: expected {last_seq}, got {raw.get('seq')}",
            ),
        )
    return issues


class AuditLog:
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
                prev_hash, seq = _prev(handle, self.path)
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
                _write_tip(_tip_path(self.path), seq=chained.seq, record_hash=chained.hash)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return chained


def verify(path: Path) -> VerifyReport:
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
    last_hash = GENESIS_HASH
    last_seq = 0
    with path.open(encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        try:
            raw_lines = handle.read().splitlines()
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
                last_hash = record.hash
                last_seq = record.seq
                expected_seq = record.seq + 1
            issues.extend(_check_tip(path, records=records, last_hash=last_hash, last_seq=last_seq))
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return VerifyReport(ok=not issues, records=records, issues=tuple(issues))


def _prev(handle: TextIO, path: Path) -> tuple[str, int]:
    cached = _load_tip(_tip_path(path))
    if cached is not None:
        return cached
    return _tail(handle)


def _load_tip(tip_path: Path) -> tuple[str, int] | None:
    try:
        raw = json.loads(tip_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    record_hash = raw.get("hash")
    seq = raw.get("seq")
    if not isinstance(record_hash, str) or _HASH.fullmatch(record_hash) is None:
        return None
    if not isinstance(seq, int) or seq < 1:
        return None
    return record_hash, seq


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
