"""Redact emails, card-like digit runs, and key material before a value is stored."""

from __future__ import annotations

import re

from capitalcite.trace.types import AttrValue

_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")
_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
_SECRET = re.compile(
    r"(?i)(?:sk-lf-[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{8,}|bearer\s+\S+|authorization\s*[:=]\s*\S+)"
)
_LEFTOVER = re.compile(r"(?i)authorization|bearer\s*|sk-[A-Za-z0-9_\-]*")


def mask_text(value: str, *, max_len: int | None = None) -> str:
    """Return value with emails, long digit runs, and key-shaped tokens removed."""
    masked = _EMAIL.sub("[REDACTED EMAIL]", value)
    masked = _CARD.sub("[REDACTED DIGITS]", masked)
    masked = _SECRET.sub("[REDACTED SECRET]", masked)
    if _LEFTOVER.search(masked):
        masked = _LEFTOVER.sub("[REDACTED SECRET]", masked)
    if max_len is not None and len(masked) > max_len:
        return masked[:max_len]
    return masked


def mask_value(value: AttrValue) -> AttrValue:
    if isinstance(value, str):
        return mask_text(value)
    return value
