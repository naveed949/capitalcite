"""Langfuse adapter. Imported only from configure when keys are present."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from capitalcite.trace.mask import mask_text
from capitalcite.trace.types import AttrValue, ObservationKind, SpanRecord

logger = logging.getLogger(__name__)

_Live = tuple[Any, Any]


def _mask_payload(*, data: object, **_kwargs: object) -> object:
    # Langfuse drops the export batch when mask raises.
    try:
        return _mask_node(data)
    except Exception:
        logger.warning("langfuse mask failed", exc_info=True)
        return data


def _mask_node(data: object) -> object:
    if isinstance(data, str):
        return mask_text(data)
    if isinstance(data, dict):
        return {str(key): _mask_node(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask_node(item) for item in data]
    return data


class LangfuseExporter:
    """Map finished spans onto Langfuse observations.

    Langfuse 5 mints its own OTel span id. Passing our span id as parent_span_id
    would point at a span that was never exported, so nesting uses
    start_as_current_observation while the request is on the stack.
    sample_rate is 1 here because the process tracer already applied
    LANGFUSE_SAMPLE_RATE. A second sample would drop spans the file exporter kept.
    """

    def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
        from langfuse import Langfuse

        self._client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=host,
            host=host,
            tracing_enabled=True,
            sample_rate=1.0,
            mask=_mask_payload,
        )
        self._live: dict[str, _Live] = {}

    def begin(
        self,
        *,
        name: str,
        kind: ObservationKind,
        span_id: str,
        attributes: Mapping[str, AttrValue],
    ) -> None:
        try:
            manager = self._client.start_as_current_observation(
                name=name,
                as_type=kind.value,
                input=_input(kind, name, attributes),
                metadata=dict(attributes),
            )
            observation = manager.__enter__()
            self._live[span_id] = (observation, manager)
        except Exception:
            logger.warning("langfuse observation start failed", exc_info=True)

    def export(self, record: SpanRecord) -> None:
        pair = self._live.pop(record.span_id, None)
        if pair is None:
            return
        observation, manager = pair
        try:
            observation.update(**_update_kwargs(record))
        except Exception:
            logger.warning("langfuse observation update failed", exc_info=True)
        finally:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.warning("langfuse observation end failed", exc_info=True)

    def shutdown(self) -> None:
        for span_id in list(self._live):
            pair = self._live.pop(span_id, None)
            if pair is None:
                continue
            _observation, manager = pair
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.warning("langfuse observation end failed", exc_info=True)
        try:
            self._client.flush()
        except Exception:
            logger.warning("langfuse flush failed", exc_info=True)
        try:
            self._client.shutdown()
        except Exception:
            logger.warning("langfuse shutdown failed", exc_info=True)


def _update_kwargs(record: SpanRecord) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "input": _input(record.kind, record.name, record.attributes),
        "output": _output(record),
        "metadata": dict(record.attributes),
        "level": "ERROR" if record.status == "error" else "DEFAULT",
    }
    if record.kind is not ObservationKind.GENERATE:
        return kwargs
    model = record.attributes.get("gen_ai.request.model")
    if isinstance(model, str):
        kwargs["model"] = model
    usage = _usage(record.attributes)
    if usage:
        kwargs["usage_details"] = usage
    cost = record.attributes.get("capitalcite.cost_usd")
    if isinstance(cost, float):
        kwargs["cost_details"] = {"total": cost}
    return kwargs


def _usage(attributes: Mapping[str, AttrValue]) -> dict[str, int]:
    usage: dict[str, int] = {}
    prompt = attributes.get("gen_ai.usage.input_tokens")
    completion = attributes.get("gen_ai.usage.output_tokens")
    if isinstance(prompt, int) and not isinstance(prompt, bool):
        usage["input"] = prompt
    if isinstance(completion, int) and not isinstance(completion, bool):
        usage["output"] = completion
    return usage


def _input(
    kind: ObservationKind,
    name: str,
    attributes: Mapping[str, AttrValue],
) -> dict[str, object] | None:
    if kind is ObservationKind.ASK:
        return _present({"question": attributes.get("capitalcite.question")})
    if kind is ObservationKind.RETRIEVE:
        return _present(
            {
                "mode": attributes.get("capitalcite.mode"),
                "collection": attributes.get("capitalcite.collection"),
            }
        )
    if kind is ObservationKind.TOOL or kind is ObservationKind.GATE:
        return _present({"tool": attributes.get("capitalcite.tool")})
    if kind is ObservationKind.EVAL and name == "eval":
        return _present(
            {
                "suite": attributes.get("capitalcite.suite"),
                "small": attributes.get("capitalcite.small"),
                "git_sha": attributes.get("capitalcite.git_sha"),
            }
        )
    return None


def _output(record: SpanRecord) -> dict[str, object] | None:
    attrs = record.attributes
    if record.kind is ObservationKind.ASK:
        return _present(
            {
                "outcome": attrs.get("capitalcite.outcome"),
                "refusal_reason": attrs.get("capitalcite.refusal_reason"),
            }
        )
    if record.kind is ObservationKind.RETRIEVE:
        hits: list[dict[str, object]] = []
        for index in range(8):
            score = attrs.get(f"capitalcite.hit.{index}.score")
            if score is None:
                continue
            hit = _present(
                {
                    "score": score,
                    "doc_id": attrs.get(f"capitalcite.hit.{index}.doc_id"),
                    "ticker": attrs.get(f"capitalcite.hit.{index}.ticker"),
                    "section": attrs.get(f"capitalcite.hit.{index}.section"),
                }
            )
            if hit is not None:
                hits.append(hit)
        payload: dict[str, object] = {"hits": hits}
        count = attrs.get("capitalcite.hit_count")
        if count is not None:
            payload["hit_count"] = count
        return payload
    if record.kind is ObservationKind.GENERATE:
        return _present(
            {
                "model": attrs.get("gen_ai.request.model"),
                "input_tokens": attrs.get("gen_ai.usage.input_tokens"),
                "output_tokens": attrs.get("gen_ai.usage.output_tokens"),
            }
        )
    if record.kind is ObservationKind.TOOL:
        return _present(
            {
                "tool": attrs.get("capitalcite.tool"),
                "ok": attrs.get("capitalcite.ok"),
            }
        )
    if record.kind is ObservationKind.GATE:
        return _present(
            {
                "decision": attrs.get("capitalcite.gate.decision"),
                "reason": attrs.get("capitalcite.gate.reason"),
            }
        )
    if record.kind is ObservationKind.EVAL:
        return _present({"config_hash": attrs.get("capitalcite.config_hash")})
    return None


def _present(values: Mapping[str, object]) -> dict[str, object] | None:
    kept = {key: value for key, value in values.items() if value is not None}
    if not kept:
        return None
    return kept
