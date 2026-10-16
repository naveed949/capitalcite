"""Process-wide tracer. observation() never raises a tracing error."""

from __future__ import annotations

import logging
import random
import subprocess
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from capitalcite.config import Settings
from capitalcite.trace.attrs import gate_attrs, sanitize
from capitalcite.trace.types import AttrValue, ObservationKind, SpanRecord

logger = logging.getLogger(__name__)

_CURRENT: ContextVar[Span | None] = ContextVar("capitalcite_trace_span", default=None)


class Exporter(Protocol):
    def begin(
        self,
        *,
        name: str,
        kind: ObservationKind,
        span_id: str,
        attributes: Mapping[str, AttrValue],
    ) -> None: ...

    def export(self, record: SpanRecord) -> None: ...

    def shutdown(self) -> None: ...


class Span(Protocol):
    def set(self, **attrs: object) -> None: ...

    def finish(self, status: Literal["ok", "error"]) -> None: ...

    @property
    def sampled(self) -> bool: ...

    @property
    def trace_id(self) -> str | None: ...

    @property
    def span_id(self) -> str | None: ...


class _NoOpSpan:
    def set(self, **attrs: object) -> None:
        return None

    def finish(self, status: Literal["ok", "error"]) -> None:
        return None

    @property
    def sampled(self) -> bool:
        return False

    @property
    def trace_id(self) -> str | None:
        return None

    @property
    def span_id(self) -> str | None:
        return None


_NOOP = _NoOpSpan()


class MemoryExporter:
    def __init__(self) -> None:
        self._finished: list[SpanRecord] = []

    def begin(
        self,
        *,
        name: str,
        kind: ObservationKind,
        span_id: str,
        attributes: Mapping[str, AttrValue],
    ) -> None:
        return None

    def export(self, record: SpanRecord) -> None:
        self._finished.append(record)

    def shutdown(self) -> None:
        return None

    def finished(self) -> tuple[SpanRecord, ...]:
        return tuple(self._finished)

    def clear(self) -> None:
        self._finished.clear()


class FileExporter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def begin(
        self,
        *,
        name: str,
        kind: ObservationKind,
        span_id: str,
        attributes: Mapping[str, AttrValue],
    ) -> None:
        return None

    def export(self, record: SpanRecord) -> None:
        line = record.model_dump_json() + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def shutdown(self) -> None:
        return None


class _MultiExporter:
    def __init__(self, exporters: Sequence[Exporter]) -> None:
        self._exporters = list(exporters)

    def begin(
        self,
        *,
        name: str,
        kind: ObservationKind,
        span_id: str,
        attributes: Mapping[str, AttrValue],
    ) -> None:
        for exporter in self._exporters:
            try:
                exporter.begin(name=name, kind=kind, span_id=span_id, attributes=attributes)
            except Exception:
                logger.warning("trace exporter begin failed", exc_info=True)

    def export(self, record: SpanRecord) -> None:
        for exporter in self._exporters:
            try:
                exporter.export(record)
            except Exception:
                logger.warning("trace exporter failed", exc_info=True)

    def shutdown(self) -> None:
        for exporter in self._exporters:
            try:
                exporter.shutdown()
            except Exception:
                logger.warning("trace exporter shutdown failed", exc_info=True)


class _RecordingSpan:
    def __init__(
        self,
        *,
        name: str,
        kind: ObservationKind,
        trace_id: str,
        span_id: str,
        parent_id: str | None,
        attributes: dict[str, AttrValue],
        exporter: Exporter,
    ) -> None:
        self._name = name
        self._kind = kind
        self._trace_id = trace_id
        self._span_id = span_id
        self._parent_id = parent_id
        self._attributes = dict(attributes)
        self._exporter = exporter
        self._start = time.perf_counter()
        self._finished = False

    def set(self, **attrs: object) -> None:
        try:
            self._attributes.update(sanitize(attrs))
        except Exception:
            logger.warning("trace attribute update failed", exc_info=True)

    def finish(self, status: Literal["ok", "error"]) -> None:
        if self._finished:
            return
        self._finished = True
        duration_ms = max(0.0, (time.perf_counter() - self._start) * 1000.0)
        self._attributes["capitalcite.latency_ms"] = duration_ms
        self._attributes["capitalcite.sampled"] = True
        record = SpanRecord(
            name=self._name,
            kind=self._kind,
            trace_id=self._trace_id,
            span_id=self._span_id,
            parent_id=self._parent_id,
            attributes=dict(self._attributes),
            status=status,
            duration_ms=duration_ms,
        )
        try:
            self._exporter.export(record)
        except Exception:
            logger.warning("trace export failed", exc_info=True)

    @property
    def sampled(self) -> bool:
        return True

    @property
    def trace_id(self) -> str | None:
        return self._trace_id

    @property
    def span_id(self) -> str | None:
        return self._span_id


class _RecordingTracer:
    def __init__(self, exporter: Exporter, sample_rate: float) -> None:
        self._exporter = exporter
        self._sample_rate = sample_rate

    def start(
        self,
        name: str,
        kind: ObservationKind,
        parent: Span | None,
        attrs: dict[str, AttrValue],
    ) -> Span:
        if parent is not None and not parent.sampled:
            return _NOOP
        if parent is None and not _root_sampled(self._sample_rate):
            return _NOOP
        trace_id = parent.trace_id if parent is not None and parent.trace_id else uuid.uuid4().hex
        parent_id = parent.span_id if parent is not None else None
        span = _RecordingSpan(
            name=name,
            kind=kind,
            trace_id=trace_id,
            span_id=uuid.uuid4().hex[:16],
            parent_id=parent_id,
            attributes=dict(attrs),
            exporter=self._exporter,
        )
        try:
            self._exporter.begin(
                name=name,
                kind=kind,
                span_id=span._span_id,
                attributes=span._attributes,
            )
        except Exception:
            logger.warning("trace begin failed", exc_info=True)
        return span

    def shutdown(self) -> None:
        self._exporter.shutdown()


class _NoOpTracer:
    def start(
        self,
        name: str,
        kind: ObservationKind,
        parent: Span | None,
        attrs: dict[str, AttrValue],
    ) -> Span:
        _ = name, kind, parent, attrs
        return _NOOP

    def shutdown(self) -> None:
        return None


_TRACER: _RecordingTracer | _NoOpTracer = _NoOpTracer()
_TEST_EXPORTER: MemoryExporter | None = None


def configure(settings: Settings, *, exporter: Exporter | None = None) -> None:
    """Install exporters. A failing adapter becomes a no-op and does not raise."""
    global _TRACER
    try:
        _TRACER.shutdown()
    except Exception:
        logger.warning("previous tracer shutdown failed", exc_info=True)
    try:
        _TRACER = _tracer_for(settings, exporter)
    except Exception:
        logger.warning("trace configure failed", exc_info=True)
        _TRACER = _NoOpTracer()


def configure_settings(settings: Settings) -> None:
    configure(settings)


def configure_for_tests(exporter: MemoryExporter, *, sample_rate: float = 1.0) -> None:
    global _TRACER, _TEST_EXPORTER
    try:
        _TRACER.shutdown()
    except Exception:
        logger.warning("previous tracer shutdown failed", exc_info=True)
    _TEST_EXPORTER = exporter
    _TRACER = _RecordingTracer(exporter, _clamp_rate(sample_rate))


def reset_for_tests() -> None:
    global _TRACER, _TEST_EXPORTER
    try:
        _TRACER.shutdown()
    except Exception:
        logger.warning("tracer reset failed", exc_info=True)
    _TEST_EXPORTER = None
    _TRACER = _NoOpTracer()
    _CURRENT.set(None)


def shutdown() -> None:
    try:
        _TRACER.shutdown()
    except Exception:
        logger.warning("tracer shutdown failed", exc_info=True)


@lru_cache(maxsize=1)
def git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        return "unknown"
    return sha


def current_trace_id() -> str | None:
    span = _CURRENT.get()
    if span is None:
        return None
    return span.trace_id


@contextmanager
def observation(name: str, kind: ObservationKind, **attrs: object) -> Iterator[Span]:
    """Open a span. Tracing failures are swallowed. The wrapped body still raises."""
    parent = _CURRENT.get()
    span: Span = _NOOP
    token: Token[Span | None] | None = None
    try:
        span = _TRACER.start(name, kind, parent, sanitize(attrs))
        token = _CURRENT.set(span)
    except Exception:
        logger.warning("trace start failed", exc_info=True)
        span = _NOOP
        token = None
    status: Literal["ok", "error"] = "ok"
    try:
        yield span
    except BaseException:
        status = "error"
        raise
    finally:
        try:
            span.finish(status)
        except Exception:
            logger.warning("trace finish failed", exc_info=True)
        if token is not None:
            _CURRENT.reset(token)


@contextmanager
def gate_decision(
    tool: str,
    decision: Literal["allow", "deny"],
    reason: str,
) -> Iterator[Span]:
    attrs = gate_attrs(tool=tool, decision=decision, reason=reason)
    with observation("gate", ObservationKind.GATE, **attrs) as span:
        yield span


def _tracer_for(settings: Settings, exporter: Exporter | None) -> _RecordingTracer | _NoOpTracer:
    if exporter is not None:
        return _RecordingTracer(exporter, _clamp_rate(settings.langfuse_sample_rate))
    exporters: list[Exporter] = []
    if _TEST_EXPORTER is not None:
        exporters.append(_TEST_EXPORTER)
    if settings.capitalcite_trace_file is not None:
        exporters.append(FileExporter(settings.capitalcite_trace_file))
    if _langfuse_wanted(settings):
        try:
            from capitalcite.trace.langfuse_exporter import LangfuseExporter

            exporters.append(
                LangfuseExporter(
                    public_key=(settings.langfuse_public_key or "").strip(),
                    secret_key=(settings.langfuse_secret_key or "").strip(),
                    host=settings.langfuse_host,
                )
            )
        except Exception:
            logger.warning("langfuse tracer disabled", exc_info=True)
    if not exporters:
        return _NoOpTracer()
    rate = _clamp_rate(settings.langfuse_sample_rate)
    if len(exporters) == 1:
        return _RecordingTracer(exporters[0], rate)
    return _RecordingTracer(_MultiExporter(exporters), rate)


def _langfuse_wanted(settings: Settings) -> bool:
    if not settings.langfuse_tracing_enabled:
        return False
    public = (settings.langfuse_public_key or "").strip()
    secret = (settings.langfuse_secret_key or "").strip()
    return bool(public and secret)


def _root_sampled(rate: float) -> bool:
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def _clamp_rate(rate: float) -> float:
    if rate < 0.0:
        return 0.0
    if rate > 1.0:
        return 1.0
    return rate
