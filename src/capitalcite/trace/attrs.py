"""Allowlisted span attributes. Unknown keys are dropped, not forwarded."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal

from capitalcite.trace.mask import mask_text, mask_value
from capitalcite.trace.types import AttrValue

if TYPE_CHECKING:
    from capitalcite.retrieval.types import Hit

_ALLOWED = frozenset(
    {
        "capitalcite.path",
        "capitalcite.outcome",
        "capitalcite.hit_count",
        "capitalcite.mode",
        "capitalcite.collection",
        "capitalcite.config_hash",
        "capitalcite.git_sha",
        "capitalcite.suite",
        "capitalcite.small",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "capitalcite.cost_usd",
        "capitalcite.latency_ms",
        "capitalcite.tool",
        "capitalcite.ok",
        "capitalcite.gate.decision",
        "capitalcite.gate.reason",
        "capitalcite.refusal_reason",
        "capitalcite.question",
        "capitalcite.sampled",
    }
)
_HIT_KEY = re.compile(r"^capitalcite\.hit\.(\d+)\.(score|doc_id|ticker|section)$")
_MAX_HITS = 8
_QUESTION_MAX = 200

_ENUM_VALUES: dict[str, frozenset[str]] = {
    "capitalcite.path": frozenset({"rag", "agent"}),
    "capitalcite.outcome": frozenset({"answered", "refused"}),
    "capitalcite.mode": frozenset({"dense", "bm25", "hybrid"}),
    "capitalcite.gate.decision": frozenset({"allow", "deny"}),
}
_INT_KEYS = frozenset(
    {
        "capitalcite.hit_count",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    }
)
_FLOAT_KEYS = frozenset({"capitalcite.cost_usd", "capitalcite.latency_ms"})
_BOOL_KEYS = frozenset({"capitalcite.small", "capitalcite.ok", "capitalcite.sampled"})

# USD per token. Only models whose public rate is written here get capitalcite.cost_usd.
_RATES_USD_PER_TOKEN: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
}


def sanitize(attrs: Mapping[str, object]) -> dict[str, AttrValue]:
    """Keep allowlisted keys. Mask strings. Drop everything else."""
    out: dict[str, AttrValue] = {}
    for key, raw in attrs.items():
        cleaned = _clean(key, raw)
        if cleaned is not None:
            out[key] = cleaned
    return out


def ask_attrs(
    *,
    path: str,
    question: str,
    outcome: str | None = None,
    refusal_reason: str | None = None,
    config_hash: str | None = None,
    git_sha: str | None = None,
) -> dict[str, AttrValue]:
    attrs: dict[str, object] = {
        "capitalcite.path": path,
        "capitalcite.question": question,
    }
    if outcome is not None:
        attrs["capitalcite.outcome"] = outcome
    if refusal_reason is not None:
        attrs["capitalcite.refusal_reason"] = refusal_reason
    if config_hash is not None:
        attrs["capitalcite.config_hash"] = config_hash
    if git_sha is not None:
        attrs["capitalcite.git_sha"] = git_sha
    return sanitize(attrs)


def retrieval_attrs(
    hits: Sequence[Hit],
    *,
    mode: str,
    collection: str,
    k: int | None = None,
) -> dict[str, AttrValue]:
    cap = _MAX_HITS if k is None else max(0, min(k, _MAX_HITS))
    attrs: dict[str, object] = {
        "capitalcite.hit_count": len(hits),
        "capitalcite.mode": mode,
        "capitalcite.collection": collection,
    }
    for index, hit in enumerate(hits[:cap]):
        attrs[f"capitalcite.hit.{index}.score"] = float(hit.score)
        attrs[f"capitalcite.hit.{index}.doc_id"] = hit.chunk.doc_id
        if hit.chunk.ticker:
            attrs[f"capitalcite.hit.{index}.ticker"] = hit.chunk.ticker
        if hit.chunk.section:
            attrs[f"capitalcite.hit.{index}.section"] = hit.chunk.section
    return sanitize(attrs)


def generation_attrs(
    *,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, AttrValue]:
    attrs: dict[str, object] = {"gen_ai.request.model": model}
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = output_tokens
    cost = _cost_usd(model, input_tokens, output_tokens)
    if cost is not None:
        attrs["capitalcite.cost_usd"] = cost
    return sanitize(attrs)


def tool_attrs(*, tool: str, ok: bool) -> dict[str, AttrValue]:
    return sanitize({"capitalcite.tool": tool, "capitalcite.ok": ok})


def gate_attrs(
    *,
    tool: str,
    decision: Literal["allow", "deny"],
    reason: str,
) -> dict[str, AttrValue]:
    return sanitize(
        {
            "capitalcite.tool": tool,
            "capitalcite.gate.decision": decision,
            "capitalcite.gate.reason": reason,
        }
    )


def eval_attrs(
    *,
    git_sha: str,
    suite: str,
    small: bool,
    config_hash: str | None = None,
) -> dict[str, AttrValue]:
    attrs: dict[str, object] = {
        "capitalcite.git_sha": git_sha,
        "capitalcite.suite": suite,
        "capitalcite.small": small,
    }
    if config_hash is not None:
        attrs["capitalcite.config_hash"] = config_hash
    return sanitize(attrs)


def _clean(key: str, raw: object) -> AttrValue | None:
    if not _allowed(key):
        return None
    if key in _ENUM_VALUES:
        if not isinstance(raw, str) or raw not in _ENUM_VALUES[key]:
            return None
        return raw
    if key in _BOOL_KEYS:
        if not isinstance(raw, bool):
            return None
        return raw
    if key in _INT_KEYS:
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return raw
    if key in _FLOAT_KEYS or _hit_field(key) == "score":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return float(raw)
    if not isinstance(raw, str):
        return None
    if key == "capitalcite.question":
        return mask_text(raw, max_len=_QUESTION_MAX)
    return mask_value(raw)


def _hit_field(key: str) -> str | None:
    match = _HIT_KEY.fullmatch(key)
    if match is None:
        return None
    return match.group(2)


def _allowed(key: str) -> bool:
    if key in _ALLOWED:
        return True
    match = _HIT_KEY.fullmatch(key)
    return match is not None and int(match.group(1)) < _MAX_HITS


def _cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    rates = _RATES_USD_PER_TOKEN.get(model)
    if rates is None or input_tokens is None or output_tokens is None:
        return None
    input_rate, output_rate = rates
    return input_tokens * input_rate + output_tokens * output_rate
