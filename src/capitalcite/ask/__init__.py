"""Structured answers over retrieved hits."""

from capitalcite.ask.ground import ground
from capitalcite.ask.guard import retrieval_refusal
from capitalcite.ask.models import (
    FakeStructuredModel,
    OpenAIStructuredModel,
    StructuredModel,
    build_structured_model,
)
from capitalcite.ask.service import ask
from capitalcite.ask.types import (
    Answer,
    Answered,
    AskError,
    AskModelName,
    Citation,
    DraftCite,
    ModelDraft,
    ModelNotConfigured,
    ModelUnavailable,
    Refused,
    draft_problem,
)

__all__ = [
    "Answer",
    "Answered",
    "AskError",
    "AskModelName",
    "Citation",
    "DraftCite",
    "FakeStructuredModel",
    "ModelDraft",
    "ModelNotConfigured",
    "ModelUnavailable",
    "OpenAIStructuredModel",
    "Refused",
    "StructuredModel",
    "ask",
    "build_structured_model",
    "draft_problem",
    "ground",
    "retrieval_refusal",
]
