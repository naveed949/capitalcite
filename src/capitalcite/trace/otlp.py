from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar, Token

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.id_generator import IdGenerator
from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import Status, StatusCode, set_span_in_context

from capitalcite.trace.types import AttrValue, ObservationKind, SpanRecord

logger = logging.getLogger(__name__)

_Pending = tuple[int, int]


class _PinnedIdGenerator(IdGenerator):
    def __init__(self) -> None:
        self._pending: ContextVar[_Pending | None] = ContextVar(
            "capitalcite_otlp_ids",
            default=None,
        )

    def pin(self, trace_id: int, span_id: int) -> Token[_Pending | None]:
        return self._pending.set((trace_id, span_id))

    def unpin(self, token: Token[_Pending | None]) -> None:
        self._pending.reset(token)

    def generate_span_id(self) -> int:
        pending = self._pending.get()
        if pending is None:
            return 1
        return pending[1]

    def generate_trace_id(self) -> int:
        pending = self._pending.get()
        if pending is None:
            return 1
        return pending[0]

    def is_trace_id_random(self) -> bool:
        return True


class OtlpExporter:
    """Langfuse 5 mints its own OTel ids. A global TracerProvider would collide
    with that.
    """

    def __init__(self, *, endpoint: str, timeout_s: float, service_name: str) -> None:
        self._ids = _PinnedIdGenerator()
        self._live: dict[str, OtelSpan] = {}
        resource = Resource.create({"service.name": service_name})
        self._provider = TracerProvider(resource=resource, id_generator=self._ids)
        self._provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint, timeout=timeout_s),
                schedule_delay_millis=200,
            )
        )
        self._tracer = self._provider.get_tracer("capitalcite")

    def begin(
        self,
        *,
        name: str,
        kind: object,
        span_id: str,
        attributes: Mapping[str, AttrValue],
        trace_id: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        if not isinstance(kind, ObservationKind):
            logger.warning("otlp span kind rejected")
            return
        if trace_id is None:
            return
        try:
            trace_int = _id_int(trace_id, 128)
            span_int = _id_int(span_id, 64)
        except ValueError:
            logger.warning("otlp span id rejected", exc_info=True)
            return
        parent = self._live.get(parent_id) if parent_id else None
        # Empty context so this span is not a child of a Langfuse OTel span.
        context: Context = Context()
        if parent is not None:
            context = set_span_in_context(parent)
        token = self._ids.pin(trace_int, span_int)
        try:
            otel_span = self._tracer.start_span(
                name,
                context=context,
                attributes=_otel_attrs(attributes),
            )
        except Exception:
            logger.warning("otlp span start failed", exc_info=True)
            return
        finally:
            self._ids.unpin(token)
        try:
            self._live[span_id] = otel_span
        except Exception:
            logger.warning("otlp span map failed", exc_info=True)
            try:
                otel_span.end()
            except Exception:
                logger.warning("otlp span end failed", exc_info=True)

    def export(self, record: SpanRecord) -> None:
        otel_span = self._live.pop(record.span_id, None)
        if otel_span is None:
            return
        try:
            otel_span.set_attributes(_otel_attrs(record.attributes))
            code = StatusCode.ERROR if record.status == "error" else StatusCode.OK
            otel_span.set_status(Status(code))
            otel_span.end()
        except Exception:
            logger.warning("otlp span end failed", exc_info=True)

    def shutdown(self) -> None:
        self._live.clear()
        try:
            self._provider.force_flush()
        except Exception:
            logger.warning("otlp flush failed", exc_info=True)
        try:
            self._provider.shutdown()
        except Exception:
            logger.warning("otlp shutdown failed", exc_info=True)


def _id_int(value: str, bits: int) -> int:
    parsed = int(value, 16)
    if parsed <= 0 or parsed.bit_length() > bits:
        raise ValueError("id out of range")
    return parsed


def _otel_attrs(attributes: Mapping[str, AttrValue]) -> dict[str, str | bool | int | float]:
    return dict(attributes)
