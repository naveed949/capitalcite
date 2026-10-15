"""10-K Item heading detection on cleaned text."""

from __future__ import annotations

import re

from capitalcite.parse.models import Span

_ITEM_LINE = re.compile(
    r"^(Item)\s+(\d{1,2}[A-Z]?)\b(?:\s*[.\-:\u2013\u2014]\s*(.*))?$",
    re.IGNORECASE | re.MULTILINE,
)
_TOC_TAIL = re.compile(r"[.\u2026]{2,}\s*\d+\s*$")
_PAGE_ONLY = re.compile(r"^[\s.\d]+$")


def detect_sections(text: str) -> tuple[Span, ...]:
    """Split ``text`` on Item headings. TOC lines with leader dots are ignored.

    Text before the first real Item is one ``body`` span so every offset
    belongs to a section.
    """
    starts: list[tuple[int, str]] = []
    for match in _ITEM_LINE.finditer(text):
        line_rest = (match.group(3) or "").strip()
        if _is_toc_line(line_rest):
            continue
        item = f"Item {match.group(2).upper()}"
        starts.append((match.start(), item))

    if not starts:
        if not text:
            return ()
        return (Span(name="body", start=0, end=len(text)),)

    spans: list[Span] = []
    first_start, _ = starts[0]
    if first_start > 0:
        spans.append(Span(name="body", start=0, end=first_start))
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        spans.append(Span(name=name, start=start, end=end))
    return tuple(spans)


def section_at(sections: tuple[Span, ...], offset: int) -> str:
    """Return the section name covering ``offset``, or ``body``."""
    for span in sections:
        if span.contains(offset):
            return span.name
    if sections and offset == sections[-1].end:
        return sections[-1].name
    return "body"


def page_at(pages: tuple[Span, ...], offset: int) -> int | None:
    """Return the 1-based page covering ``offset``, if pages were recorded."""
    for span in pages:
        if span.contains(offset):
            return int(span.name)
    if pages and offset == pages[-1].end:
        return int(pages[-1].name)
    return None


def _is_toc_line(rest: str) -> bool:
    if not rest:
        return False
    if _TOC_TAIL.search(rest):
        return True
    return bool(_PAGE_ONLY.match(rest))
