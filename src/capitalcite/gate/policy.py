"""Load the allow-list. A broken file is a deny-all policy, not an exception."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from capitalcite.gate.schema import unsupported_schema


class ToolRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    max_calls: int = Field(ge=1, le=100_000)
    max_arg_bytes: int = Field(ge=1, le=1_000_000)
    args: dict[str, object]

    @model_validator(mode="after")
    def args_schema_is_supported(self) -> ToolRule:
        error = unsupported_schema(self.args)
        if error is not None:
            raise ValueError(error)
        return self


class Policy(BaseModel):
    """Allow-list. `default` can only be deny. A default-allow file does not load."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: Literal[1]
    default: Literal["deny"]
    tools: dict[str, ToolRule]


class BrokenPolicy:
    """Load failed. Every decide() is a deny with reason policy_load_failed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail


def default_policy_path() -> Path:
    return Path(__file__).resolve().parent / "tools.json"


def load_policy(path: Path | None) -> Policy | BrokenPolicy:
    resolved = path if path is not None else default_policy_path()
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        return BrokenPolicy(detail=str(exc) or type(exc).__name__)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return BrokenPolicy(detail=str(exc) or "invalid JSON")
    try:
        return Policy.model_validate(payload)
    except ValidationError as exc:
        return BrokenPolicy(detail=str(exc))
