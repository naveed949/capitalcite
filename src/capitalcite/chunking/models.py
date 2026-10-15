from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ChunkerName(StrEnum):
    FIXED = "fixed"
    RECURSIVE = "recursive"
    SECTION = "section"


class Chunk(BaseModel):
    doc_id: str = Field(min_length=1)
    section: str = Field(min_length=1)
    offset: int = Field(ge=0)
    source_url: str = Field(min_length=1)
    text: str = Field(min_length=1)
    token_count: int = Field(ge=0)
    chunker: ChunkerName
    page: int | None = Field(default=None, ge=1)
    ticker: str = ""
    form: str = ""
    period: str = ""
    accession: str = ""


class ChunkStats(BaseModel):
    chunker: ChunkerName
    count: int = Field(ge=0)
    mean_tokens: float = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @classmethod
    def from_chunks(cls, chunker: ChunkerName, chunks: list[Chunk]) -> ChunkStats:
        total = sum(chunk.token_count for chunk in chunks)
        mean = (total / len(chunks)) if chunks else 0.0
        return cls(chunker=chunker, count=len(chunks), mean_tokens=mean, total_tokens=total)
