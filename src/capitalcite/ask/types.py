"""Drafts the model emits, and the answer the server is willing to return."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AskError(Exception):
    """Domain failure. Routes map these. Ask does not raise HTTPException."""


class ModelNotConfigured(AskError):
    """ASK_MODEL=openai and OPENAI_API_KEY is empty."""


class ModelUnavailable(AskError):
    """The chat completion request failed."""


class DraftCite(BaseModel):
    """A citation the model claims. index selects a retrieved hit."""

    model_config = ConfigDict(frozen=True)
    index: int
    quote: str


class ModelDraft(BaseModel):
    """Untrusted model output. Parse it at the adapter. ground() still checks it."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["answered", "refused"]
    answer: str = ""
    refusal_reason: str = ""
    confidence: float = 0.0
    citations: tuple[DraftCite, ...] = ()

    @model_validator(mode="after")
    def legal_shape(self) -> ModelDraft:
        problem = draft_problem(self)
        if problem is not None:
            raise ValueError(problem)
        return self


class Citation(BaseModel):
    """A grounded citation. start and end are the chunk span, not the quote span."""

    model_config = ConfigDict(frozen=True)
    doc_id: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str = Field(min_length=1)
    section: str = Field(min_length=1)
    source_url: str = Field(min_length=1)


class Answered(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["answered"] = "answered"
    answer: str = Field(min_length=1)
    citations: tuple[Citation, ...] = Field(min_length=1)
    confidence: float


class Refused(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["refused"] = "refused"
    refusal_reason: str = Field(min_length=1)
    confidence: float = 0.0


Answer = Annotated[Answered | Refused, Field(discriminator="kind")]


def draft_problem(draft: ModelDraft) -> str | None:
    """Return why this draft is not a legal answered or refused shape."""
    if draft.kind == "answered":
        if not draft.answer.strip():
            return "answered draft needs a non-empty answer"
        if not draft.citations:
            return "answered draft needs at least one citation"
        return None
    if not draft.refusal_reason.strip():
        return "refused draft needs a non-empty reason"
    if draft.citations:
        return "refused draft must not include citations"
    return None
