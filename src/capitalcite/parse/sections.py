from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.parse.models import Span

_ITEM = re.compile(
    r"^(?:Part\s+[IVXLC0-9]+(?:\s*[.\-:\u2013\u2014]\s*)?)?(Item)\s+"
    r"(\d{1,2}[A-Z]?)\b(?:\s*[.\-:\u2013\u2014]\s*(.*))?$",
    re.IGNORECASE,
)
_ITEM_HERE = re.compile(
    r"(?:Part\s+[IVXLC0-9]+(?:\s*[.\-:\u2013\u2014]\s*)?)?Item\s+"
    r"(\d{1,2}[A-Z]?)\b(?:\s*[.\-:\u2013\u2014]\s*)?",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^(\d{1,2}[A-Z]?)\.\s*$", re.IGNORECASE)
_TOC_TAIL = re.compile(r"[.\u2026]{2,}\s*\d+\s*$")
_PAGE_ONLY = re.compile(r"^[\s.\d]+$")
_XREF = re.compile(r"^see\s+pages?\s+\d", re.IGNORECASE)
_POINTER = re.compile(
    r"appears on pages?|incorporated by reference|of this (?:annual report|form 10-k)",
    re.IGNORECASE,
)
_REFER = re.compile(r"^refer to\b|^see\b", re.IGNORECASE)
_INAUGURAL = re.compile(r"the following is management", re.IGNORECASE)
_PAGE_CELL = re.compile(
    r"^\d{1,3}(?:\s*[\u2013\u2014\-]\s*\d{1,3})?"
    r"(?:\s*,\s*\d{1,3}(?:\s*[\u2013\u2014\-]\s*\d{1,3})?)*$",
)
_TABLE_RULE = re.compile(r"^:?-{3,}:?$")
_PROSE_START = re.compile(r"^(As|The|We|Our|This|In)\b")
_FOLLOW_WINDOW = 400

# Native titles that mean a 10-K Item when the filing never says "Item 7".
_ALIASES: tuple[tuple[str, str], ...] = (
    ("management's discussion and analysis", "Item 7"),
    ("financial review", "Item 7"),
    ("consolidated financial statements", "Item 8"),
    ("notes to consolidated financial statements", "Item 8"),
)


class CandidateKind(StrEnum):
    TOC = "toc"
    POINTER = "pointer"
    RUNNING_HEADER = "running_header"
    BODY = "body"


class HeadingCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    start: int = Field(ge=0)
    name: str = Field(min_length=1)
    rest: str
    kind: CandidateKind
    alias: bool = False


def detect_sections(text: str) -> tuple[Span, ...]:
    """Return non-overlapping Item spans. Text is not modified."""
    starts = [(c.start, c.name) for c in _body_starts(text)]
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
    for span in sections:
        if span.contains(offset):
            return span.name
    if sections and offset == sections[-1].end:
        return sections[-1].name
    return "body"


def page_at(pages: tuple[Span, ...], offset: int) -> int | None:
    for span in pages:
        if span.contains(offset):
            return int(span.name)
    if pages and offset == pages[-1].end:
        return int(pages[-1].name)
    return None


def collect_candidates(text: str) -> tuple[HeadingCandidate, ...]:
    found: list[HeadingCandidate] = []
    for line_start, line in _lines(text):
        found.extend(_candidates_in_line(text, line_start, line))
    classified = [_classify(text, c) for c in found]
    return tuple(classified)


def _body_starts(text: str) -> list[HeadingCandidate]:
    kept: list[HeadingCandidate] = []
    seen_body: set[str] = set()
    for candidate in collect_candidates(text):
        if candidate.kind is not CandidateKind.BODY:
            continue
        if kept and candidate.start - kept[-1].start < 80 and candidate.name == kept[-1].name:
            continue
        if candidate.alias and candidate.name in seen_body:
            continue
        kept.append(candidate)
        seen_body.add(candidate.name)
    return kept


def _candidates_in_line(text: str, line_start: int, line: str) -> list[HeadingCandidate]:
    stripped = line.strip()
    if not stripped or _TABLE_RULE.match(stripped.replace("|", "").strip()):
        return []
    if "|" in line:
        return _candidates_in_table_row(text, line_start, line)
    found = _items_in_unit(line_start, line, stripped)
    if found:
        return found
    match = _match_alias(stripped)
    if match is None:
        return []
    name, rest = match
    start = line_start + line.find(stripped)
    return [
        HeadingCandidate(start=start, name=name, rest=rest, kind=CandidateKind.BODY, alias=True)
    ]


def _candidates_in_table_row(text: str, line_start: int, line: str) -> list[HeadingCandidate]:
    cells = _cells(line)
    if not cells:
        return []
    page_row = _toc_table_row(cells)
    joined = " ".join(cells)
    found: list[HeadingCandidate] = []
    for cell in cells:
        match = _match_unit(cell) or _match_numbered_title(cell, cells)
        if match is None:
            continue
        name, rest, alias = match
        loc = line.find(cell)
        start = line_start + loc if loc >= 0 else line_start
        kind = CandidateKind.TOC if page_row else CandidateKind.BODY
        found.append(
            HeadingCandidate(start=start, name=name, rest=rest or joined, kind=kind, alias=alias)
        )
    if found:
        return found
    match = _match_unit(joined)
    if match is None:
        return []
    name, rest, alias = match
    loc = line.find(cells[0])
    start = line_start + loc if loc >= 0 else line_start
    kind = CandidateKind.TOC if page_row else CandidateKind.BODY
    return [HeadingCandidate(start=start, name=name, rest=rest or joined, kind=kind, alias=alias)]


_PART_ONLY = re.compile(
    r"^(?:Part\s+[IVXLC0-9]+(?:\s*[.\-:\u2013\u2014]\s*)?)?$",
    re.IGNORECASE,
)


def _items_in_unit(line_start: int, line: str, unit: str) -> list[HeadingCandidate]:
    matches = list(_ITEM_HERE.finditer(unit))
    matches = [match for match in matches if _heading_item(unit, match)]
    if not matches:
        return []
    found: list[HeadingCandidate] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(unit)
        rest = unit[match.end() : end].strip()
        unit_at = line.find(unit)
        start = line_start + (unit_at if unit_at >= 0 else 0) + match.start()
        found.append(
            HeadingCandidate(
                start=start,
                name=f"Item {match.group(1).upper()}",
                rest=rest,
                kind=CandidateKind.BODY,
                alias=False,
            )
        )
    return found


def _heading_item(unit: str, match: re.Match[str]) -> bool:
    prefix = unit[: match.start()]
    if _PART_ONLY.match(prefix.strip()) is not None:
        return True
    return bool(_ITEM_HERE.search(prefix))


def _match_unit(unit: str) -> tuple[str, str, bool] | None:
    item = _ITEM.match(unit)
    if item is not None:
        return f"Item {item.group(2).upper()}", (item.group(3) or "").strip(), False
    alias = _match_alias(unit)
    if alias is not None:
        return alias[0], alias[1], True
    return None


def _match_numbered_title(cell: str, cells: list[str]) -> tuple[str, str, bool] | None:
    numbered = _NUMBERED.match(cell)
    if numbered is None:
        return None
    item = f"Item {numbered.group(1).upper()}"
    title = next((other for other in cells if _match_alias(other) is not None), "")
    return item, title, False


def _match_alias(unit: str) -> tuple[str, str] | None:
    folded = _fold(unit)
    for needle, name in _ALIASES:
        if folded == needle or folded.startswith(needle):
            return name, unit
    return None


def _classify(text: str, candidate: HeadingCandidate) -> HeadingCandidate:
    if candidate.kind is CandidateKind.TOC:
        return candidate
    rest = candidate.rest.strip()
    follow = _follow_text(text, candidate.start)
    if _is_toc_line(rest) or _is_toc_line(follow.split("\n", 1)[0].strip()):
        return candidate.model_copy(update={"kind": CandidateKind.TOC})
    if _POINTER.search(rest) or _POINTER.search(follow[:_FOLLOW_WINDOW]):
        return candidate.model_copy(update={"kind": CandidateKind.POINTER})
    if _REFER.match(rest) or _REFER.match(follow.lstrip()) or _XREF.match(rest):
        return candidate.model_copy(update={"kind": CandidateKind.POINTER})
    if candidate.alias and candidate.name == "Item 7":
        folded = _fold(rest)
        short = folded in {
            "management's discussion and analysis",
            "financial review",
        }
        if short and not _INAUGURAL.search(follow[:_FOLLOW_WINDOW]):
            if folded == "financial review":
                return candidate
            return candidate.model_copy(update={"kind": CandidateKind.RUNNING_HEADER})
    return candidate


def _follow_text(text: str, start: int) -> str:
    line_end = text.find("\n", start)
    if line_end < 0:
        return ""
    return text[line_end + 1 : line_end + 1 + _FOLLOW_WINDOW]


def _is_toc_line(rest: str) -> bool:
    if not rest:
        return False
    if _TOC_TAIL.search(rest):
        return True
    if _XREF.match(rest):
        return True
    if _PAGE_CELL.match(rest):
        return True
    return bool(_PAGE_ONLY.match(rest))


def _toc_table_row(cells: list[str]) -> bool:
    pages = [cell for cell in cells if _PAGE_CELL.match(cell)]
    if not pages:
        return False
    if any("," in cell or re.search(r"[\u2013\u2014\-]", cell) for cell in pages):
        return True
    return not any(_looks_like_prose(cell) for cell in cells)


def _looks_like_prose(cell: str) -> bool:
    return len(cell) > 160 or ". " in cell or _PROSE_START.match(cell) is not None


def _cells(line: str) -> list[str]:
    parts = [part.strip() for part in line.split("|")]
    return [part for part in parts if part and not _TABLE_RULE.match(part)]


def _lines(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        rows.append((pos, line.rstrip("\r\n")))
        pos += len(line)
    return rows


def _fold(value: str) -> str:
    return (
        value.replace("\u2019", "'").replace("\u2018", "'").replace("\xa0", " ").casefold().strip()
    )
