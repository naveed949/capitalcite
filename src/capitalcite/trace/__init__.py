"""Optional tracing. No-op when Langfuse is not configured. Never fails a request."""

from capitalcite.trace.attrs import (
    ask_attrs,
    eval_attrs,
    gate_attrs,
    generation_attrs,
    retrieval_attrs,
    tool_attrs,
)
from capitalcite.trace.runtime import (
    FileExporter,
    MemoryExporter,
    Span,
    configure,
    configure_for_tests,
    configure_settings,
    current_trace_id,
    git_sha,
    gate_decision,
    observation,
    reset_for_tests,
    shutdown,
)
from capitalcite.trace.types import ObservationKind, SpanRecord

__all__ = [
    "FileExporter",
    "MemoryExporter",
    "ObservationKind",
    "Span",
    "SpanRecord",
    "ask_attrs",
    "configure",
    "configure_for_tests",
    "configure_settings",
    "current_trace_id",
    "git_sha",
    "eval_attrs",
    "gate_attrs",
    "gate_decision",
    "generation_attrs",
    "observation",
    "reset_for_tests",
    "retrieval_attrs",
    "shutdown",
    "tool_attrs",
]
