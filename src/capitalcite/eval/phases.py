"""Fold observation() spans into per-query phases. No second clock."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.trace.types import ObservationKind, SpanRecord


class PhaseTimes(BaseModel):
    model_config = ConfigDict(frozen=True)
    ttft_ms: float = Field(ge=0.0)
    tool_ms: float = Field(ge=0.0)
    retrieval_ms: float = Field(ge=0.0)
    model_ms: float = Field(ge=0.0)
    compile_ms: float = Field(ge=0.0)
    total_ms: float = Field(ge=0.0)


def fold_phases(
    spans: Sequence[SpanRecord],
    begins: Mapping[str, float] | None = None,
) -> PhaseTimes:
    """Retrieve is exclusive of tool. Nested retrieve time is not double-counted."""
    started = dict(begins) if begins is not None else {}
    retrieve = [span for span in spans if span.kind is ObservationKind.RETRIEVE]
    generate = [span for span in spans if span.kind is ObservationKind.GENERATE]
    toolish = [
        span
        for span in spans
        if span.kind in {ObservationKind.TOOL, ObservationKind.GATE}
    ]
    compile_spans = [span for span in spans if span.name == "compile"]
    retrieval_ms = sum(span.duration_ms for span in retrieve)
    model_ms = sum(span.duration_ms for span in generate)
    compile_ms = sum(span.duration_ms for span in compile_spans)
    tool_ms = 0.0
    for span in toolish:
        nested = sum(
            child.duration_ms for child in retrieve if child.parent_id == span.span_id
        )
        tool_ms += max(0.0, span.duration_ms - nested)
    roots = [span for span in spans if span.kind is ObservationKind.ASK]
    if not roots:
        roots = [
            span
            for span in spans
            if span.kind is ObservationKind.AGENT and span.name == "agent"
        ]
    if not roots:
        roots = [span for span in spans if span.parent_id is None]
    total_ms = max((span.duration_ms for span in roots), default=0.0)
    ttft_ms = _ttft(roots, generate, started, total_ms)
    return PhaseTimes(
        ttft_ms=ttft_ms,
        tool_ms=tool_ms,
        retrieval_ms=retrieval_ms,
        model_ms=model_ms,
        compile_ms=compile_ms,
        total_ms=total_ms,
    )


def _ttft(
    roots: Sequence[SpanRecord],
    generate: Sequence[SpanRecord],
    started: Mapping[str, float],
    total_ms: float,
) -> float:
    if not generate:
        return 0.0
    first = min(generate, key=lambda span: started.get(span.span_id, float("inf")))
    if roots:
        root = roots[0]
        root_begin = started.get(root.span_id)
        gen_begin = started.get(first.span_id)
        if root_begin is not None and gen_begin is not None:
            return max(0.0, (gen_begin - root_begin) * 1000.0)
    return max(0.0, total_ms - first.duration_ms)
