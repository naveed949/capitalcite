"""Canonical JSON SHA-256. Digests are hex, lowercase, 64 characters."""

from __future__ import annotations

import hashlib
import json
from typing import Any

HEX64 = 64


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(value: object) -> str:
    """SHA-256 hex of canonical JSON. Used for args, outputs, and chain hashes."""
    return sha256_hex(canonical_json(value).encode("utf-8"))


def sha256_json(payload: dict[str, Any]) -> str:
    return digest(payload)
