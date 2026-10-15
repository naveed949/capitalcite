"""Domain types for one golden Q&A item.

`evidence` lists passages a retriever should return (recall@k / MRR).
`answer` is scored separately for correctness. An evidence row with
role=answer is the span the gold answer is taken from. role=relevant
marks a passage that should be retrieved but does not contain the span.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class QuestionType(StrEnum):
    EXTRACTIVE = "extractive"
    NUMERIC = "numeric"
    COMPARISON = "comparison"
    MULTI_HOP = "multi_hop"
    UNANSWERABLE = "unanswerable"
    TRICK = "trick"


class ItemSource(StrEnum):
    HANDWRITTEN = "handwritten"
    FINANCEBENCH = "financebench"
    FINQA = "finqa"


class EvidenceRole(StrEnum):
    ANSWER = "answer"
    RELEVANT = "relevant"


class Evidence(BaseModel):
    """One labeled passage. `doc_id` is an EDGAR accession when the
    filing is in the ingest set, otherwise the benchmark document name.
    """

    doc_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    role: EvidenceRole = EvidenceRole.ANSWER


class GoldenItem(BaseModel):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    type: QuestionType
    answerable: bool
    source: ItemSource = ItemSource.HANDWRITTEN

    @model_validator(mode="after")
    def check_answerability(self) -> GoldenItem:
        if self.answerable:
            if not self.answer.strip():
                raise ValueError(f"{self.id}: answerable item needs a non-empty answer")
            if not self.evidence:
                raise ValueError(f"{self.id}: answerable item needs evidence")
            if not any(row.role is EvidenceRole.ANSWER for row in self.evidence):
                raise ValueError(f"{self.id}: answerable item needs an answer-role quote")
            if self.type is QuestionType.UNANSWERABLE:
                raise ValueError(f"{self.id}: unanswerable type cannot be answerable")
        else:
            if self.type not in {QuestionType.UNANSWERABLE, QuestionType.TRICK}:
                raise ValueError(f"{self.id}: non-answerable item must be unanswerable or trick")
            if not self.answer.strip():
                raise ValueError(f"{self.id}: refusal items still need an explicit answer string")
        return self

    @property
    def is_numeric(self) -> bool:
        return self.type in {QuestionType.NUMERIC, QuestionType.COMPARISON}

    @property
    def is_unanswerable_or_trick(self) -> bool:
        return self.type in {QuestionType.UNANSWERABLE, QuestionType.TRICK}


class GoldenStats(BaseModel):
    items: int
    answerable: int
    unanswerable_or_trick: int
    numeric: int
    handwritten: int
    financebench: int
    finqa: int
    unanswerable_or_trick_pct: float
    numeric_pct: float
    types: dict[str, int]
    sources: dict[str, int]
