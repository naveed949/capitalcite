"""The one public ask function. Retrieval stays in the route."""

from __future__ import annotations

from capitalcite.ask.ground import ground
from capitalcite.ask.models import StructuredModel
from capitalcite.ask.types import Answer, Refused
from capitalcite.retrieval.types import Hit


async def ask(hits: list[Hit], question: str, model: StructuredModel) -> Answer:
    """Ground one draft. Empty hits refuse without calling the model."""
    if not hits:
        return Refused(refusal_reason="no passages were retrieved")
    draft = await model.complete(question, hits)
    return ground(draft, hits)
