from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from capitalcite.audit.digest import HEX64

Decision = Literal["allowed", "denied"]
GENESIS_HASH = "0" * HEX64
_HASH_FIELD = r"^[0-9a-f]{64}$"
AUDIT_UNAVAILABLE = "audit_unavailable"
AUDIT_UNAVAILABLE_DETAIL = "audit unavailable"


class AuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    seq: int = Field(ge=1)
    ts: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    args_digest: str = Field(pattern=_HASH_FIELD)
    decision: Decision
    reason: str | None = None
    output_digest: str = Field(pattern=_HASH_FIELD)
    prev_hash: str = Field(pattern=_HASH_FIELD)
    hash: str = Field(pattern=_HASH_FIELD)

    @model_validator(mode="after")
    def legal_shape(self) -> AuditRecord:
        if self.decision == "allowed":
            if self.reason is not None:
                raise ValueError("allowed must not include a deny reason")
            return self
        if self.reason is None or not self.reason.strip():
            raise ValueError("denied needs a reason")
        return self


class VerifyIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    seq: int | None
    reason: str


class VerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    ok: bool
    records: int = Field(ge=0)
    issues: tuple[VerifyIssue, ...] = ()
