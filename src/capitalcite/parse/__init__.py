from __future__ import annotations

from pathlib import Path

from capitalcite.parse.html import html_to_document, table_to_markdown
from capitalcite.parse.models import CitationSource, ParsedDocument, Span
from capitalcite.parse.pdf import pdf_to_document
from capitalcite.parse.sections import detect_sections, page_at, section_at

__all__ = [
    "CitationSource",
    "ParsedDocument",
    "Span",
    "detect_sections",
    "html_to_document",
    "page_at",
    "parse_bytes",
    "parse_path",
    "pdf_to_document",
    "section_at",
    "table_to_markdown",
]


def parse_path(path: Path, source: CitationSource) -> ParsedDocument:
    return parse_bytes(path.read_bytes(), source, filename=path.name)


def parse_bytes(data: bytes, source: CitationSource, *, filename: str = "") -> ParsedDocument:
    if filename.lower().endswith(".pdf"):
        return pdf_to_document(data, source)
    return html_to_document(data, source)
