"""HTML filings to cleaned text. Tables become markdown."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from capitalcite.parse.models import CitationSource, ParsedDocument
from capitalcite.parse.sections import detect_sections

_WS = re.compile(r"[ \t]+")
_BLANK = re.compile(r"\n{3,}")


def html_to_document(data: bytes, source: CitationSource) -> ParsedDocument:
    """Parse HTML bytes into cleaned text with tables as markdown."""
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    _unwrap_single_cell_tables(soup)
    tables: list[str] = []
    for table in reversed(soup.find_all("table")):
        if not isinstance(table, Tag):
            continue
        markdown = table_to_markdown(table)
        if markdown:
            tables.append(markdown)
            table.replace_with(NavigableString(f"\n\n{markdown}\n\n"))
        else:
            table.decompose()
    text = _normalize_text(soup.get_text("\n"))
    return ParsedDocument(
        source=source,
        text=text,
        media_type="html",
        sections=detect_sections(text),
        pages=(),
        tables=tuple(reversed(tables)),
    )


def table_to_markdown(table: Tag) -> str:
    """Render one HTML table as a GitHub-style markdown table."""
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        if not isinstance(row, Tag):
            continue
        if row.find_parent("table") is not table:
            continue
        cells = [
            _cell_text(cell)
            for cell in row.find_all(["th", "td"], recursive=False)
            if isinstance(cell, Tag)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in padded[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _unwrap_single_cell_tables(soup: BeautifulSoup) -> None:
    changed = True
    while changed:
        changed = False
        for table in list(soup.find_all("table")):
            if not isinstance(table, Tag):
                continue
            nested = [child for child in table.find_all("table") if child is not table]
            if not nested:
                continue
            cells = table.find_all(["td", "th"])
            if len(cells) <= 1:
                table.unwrap()
                changed = True


def _cell_text(cell: Tag) -> str:
    text = _WS.sub(" ", cell.get_text(" ", strip=True))
    return text.replace("|", "\\|")


def _normalize_text(text: str) -> str:
    lines = [_WS.sub(" ", line).rstrip() for line in text.splitlines()]
    joined = "\n".join(lines)
    return _BLANK.sub("\n\n", joined).strip()
