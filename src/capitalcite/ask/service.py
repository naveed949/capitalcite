"""The one public ask function. Retrieval stays in the route."""

from __future__ import annotations

from capitalcite.ask.ground import ground
from capitalcite.ask.guard import retrieval_refusal
from capitalcite.ask.models import StructuredModel
from capitalcite.ask.types import Answer, Refused
from capitalcite.retrieval.types import Hit


async def ask(
    hits: list[Hit],
    question: str,
    model: StructuredModel,
    *,
    min_score: float = 0.0,
) -> Answer:
    """Ground one draft. Weak or mismatched retrieval refuses without a model call."""
    reason = retrieval_refusal(hits, question, min_score=min_score)
    if reason is not None:
        return Refused(refusal_reason=reason)
    draft = await model.complete(question, hits)
    return ground(draft, hits, question)
