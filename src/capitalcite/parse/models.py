"""Parsed filing text and the citation fields later chunks inherit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CitationSource(BaseModel):
    """Identity copied onto every chunk produced from one document."""

    doc_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    ticker: str = ""
    form: str = ""
    period: str = ""
    accession: str = ""


class Span(BaseModel):
    """Half-open character range ``[start, end)`` in cleaned document text."""

    name: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


class ParsedDocument(BaseModel):
    """Cleaned text plus locators. Chunkers read this, not raw HTML."""

    source: CitationSource
    text: str
    media_type: Literal["html", "pdf"]
    sections: tuple[Span, ...] = ()
    pages: tuple[Span, ...] = ()
    tables: tuple[str, ...] = ()
