from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from capitalcite.parse.models import CitationSource, ParsedDocument, Span
from capitalcite.parse.sections import detect_sections


def pdf_to_document(data: bytes, source: CitationSource) -> ParsedDocument:
    """Extract text per page. Table structure is not recovered from PDF bytes."""
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []
    pages: list[Span] = []
    cursor = 0
    for index, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text() or ""
        piece = extracted.strip()
        if parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(piece)
        cursor += len(piece)
        pages.append(Span(name=str(index), start=start, end=cursor))
    text = "".join(parts).strip()
    if text and pages and pages[-1].end != len(text):
        pages[-1] = Span(name=pages[-1].name, start=pages[-1].start, end=len(text))
    return ParsedDocument(
        source=source,
        text=text,
        media_type="pdf",
        sections=detect_sections(text),
        pages=tuple(pages),
        tables=(),
    )
