from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens, token_chars
from capitalcite.parse.models import ParsedDocument
from capitalcite.parse.sections import page_at, section_at

_SEPARATORS = ("\n\n", "\n", ". ", " ", "")
_TABLE_SEP = re.compile(r"^:?-{3,}:?$")


@dataclass(frozen=True)
class _Unit:
    start: int
    end: int
    header_prefix: str = ""


def chunk_fixed(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    _check_budgets(size, overlap)
    if not document.text.strip():
        return []
    window = token_chars(size)
    overlap_chars = token_chars(overlap)
    units = _collect(document.text, 0, len(document.text), window, overlap_chars, "fixed")
    return _chunks_from_units(document, units, ChunkerName.FIXED)


def chunk_recursive(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    _check_budgets(size, overlap)
    if not document.text.strip():
        return []
    window = token_chars(size)
    overlap_chars = token_chars(overlap)
    units = _collect(document.text, 0, len(document.text), window, overlap_chars, "recursive")
    return _pack(document, units, size=size, overlap=overlap, chunker=ChunkerName.RECURSIVE)


def chunk_section(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    _check_budgets(size, overlap)
    spans = document.sections
    if not spans:
        return chunk_recursive(document, size=size, overlap=overlap)
    window = token_chars(size)
    overlap_chars = token_chars(overlap)
    chunks: list[Chunk] = []
    for span in spans:
        piece = document.text[span.start : span.end]
        if not piece.strip():
            continue
        if count_tokens(piece) <= size:
            chunk = _make_chunk(document, span.start, span.end, ChunkerName.SECTION)
            if chunk is not None:
                chunks.append(chunk)
            continue
        units = _collect(document.text, span.start, span.end, window, overlap_chars, "recursive")
        packed = _pack(document, units, size=size, overlap=overlap, chunker=ChunkerName.SECTION)
        chunks.extend(packed)
    return chunks


CHUNKERS: dict[ChunkerName, Callable[..., list[Chunk]]] = {
    ChunkerName.FIXED: chunk_fixed,
    ChunkerName.RECURSIVE: chunk_recursive,
    ChunkerName.SECTION: chunk_section,
}


def _check_budgets(size: int, overlap: int) -> None:
    if size < 1:
        raise ValueError("chunk size must be at least 1 token")
    if overlap < 0:
        raise ValueError("chunk overlap cannot be negative")
    if overlap >= size:
        raise ValueError("chunk overlap must be smaller than chunk size")


def _make_chunk(
    document: ParsedDocument,
    start: int,
    end: int,
    chunker: ChunkerName,
    header_prefix: str = "",
) -> Chunk | None:
    original = document.text[start:end]
    text = f"{header_prefix}{original}" if header_prefix else original
    if not text.strip():
        return None
    leading = len(original) - len(original.lstrip()) if original.strip() else 0
    offset = start + leading
    source = document.source
    return Chunk(
        doc_id=source.doc_id,
        section=section_at(document.sections, offset),
        start=start,
        end=end,
        offset=offset,
        source_url=source.source_url,
        text=text,
        token_count=count_tokens(text),
        chunker=chunker,
        header_prefix=header_prefix,
        page=page_at(document.pages, offset),
        ticker=source.ticker,
        form=source.form,
        period=source.period,
        accession=source.accession,
    )


def _collect(
    text: str,
    lo: int,
    hi: int,
    max_chars: int,
    overlap_chars: int,
    mode: str,
) -> list[_Unit]:
    units: list[_Unit] = []
    for start, end, kind in _partition(text, lo, hi):
        if kind == "table":
            units.extend(_table_units(text, start, end, max_chars))
        elif mode == "fixed":
            units.extend(_window_units(start, end, max_chars, overlap_chars))
        else:
            units.extend(_recursive_units(text, start, end, max_chars, 0))
    return units


def _partition(text: str, lo: int, hi: int) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    pos = lo
    while pos < hi:
        line_end = _line_end(text, pos, hi)
        if _is_table_line(text[pos:line_end]):
            end = line_end
            while end < hi:
                nxt = _line_end(text, end, hi)
                if not _is_table_line(text[end:nxt]):
                    break
                end = nxt
            blocks.append((pos, end, "table"))
            pos = end
            continue
        end = line_end
        while end < hi:
            nxt = _line_end(text, end, hi)
            if _is_table_line(text[end:nxt]):
                break
            end = nxt
        blocks.append((pos, end, "prose"))
        pos = end
    return blocks


def _line_end(text: str, start: int, hi: int) -> int:
    newline = text.find("\n", start, hi)
    if newline == -1:
        return hi
    return newline + 1


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.count("|") >= 2


def _is_sep_line(line: str) -> bool:
    stripped = line.strip().strip("|")
    cells = [cell.strip() for cell in stripped.split("|")]
    return bool(cells) and all(_TABLE_SEP.fullmatch(cell) is not None for cell in cells)


def _table_units(text: str, lo: int, hi: int, max_chars: int) -> list[_Unit]:
    if hi - lo <= max_chars:
        return [_Unit(lo, hi)]
    rows: list[tuple[int, int]] = []
    pos = lo
    while pos < hi:
        end = _line_end(text, pos, hi)
        rows.append((pos, end))
        pos = end
    if len(rows) < 3 or not _is_sep_line(text[rows[1][0] : rows[1][1]]):
        return [_Unit(lo, hi)]
    header_prefix = text[rows[0][0] : rows[1][1]]
    units: list[_Unit] = []
    piece_start = rows[0][0]
    piece_end = rows[1][1]
    prefixed = False
    for row in rows[2:]:
        if prefixed:
            tentative = len(header_prefix) + (row[1] - piece_start)
        else:
            tentative = row[1] - piece_start
        has_rows = piece_end > (rows[1][1] if not prefixed else piece_start)
        if has_rows and tentative > max_chars:
            units.append(_Unit(piece_start, piece_end, header_prefix if prefixed else ""))
            piece_start = row[0]
            piece_end = row[1]
            prefixed = True
        else:
            piece_end = row[1]
    if piece_end > piece_start:
        units.append(_Unit(piece_start, piece_end, header_prefix if prefixed else ""))
    return units


def _window_units(lo: int, hi: int, max_chars: int, overlap_chars: int) -> list[_Unit]:
    if hi <= lo:
        return []
    if hi - lo <= max_chars:
        return [_Unit(lo, hi)]
    stride = max(1, max_chars - overlap_chars)
    units: list[_Unit] = []
    start = lo
    while start < hi:
        end = min(start + max_chars, hi)
        units.append(_Unit(start, end))
        if end >= hi:
            break
        start += stride
    return units


def _recursive_units(text: str, lo: int, hi: int, max_chars: int, sep_index: int) -> list[_Unit]:
    if hi <= lo:
        return []
    if hi - lo <= max_chars or sep_index >= len(_SEPARATORS):
        if hi - lo <= max_chars:
            return [_Unit(lo, hi)]
        return _window_units(lo, hi, max_chars, 0)
    sep = _SEPARATORS[sep_index]
    if sep == "":
        return _window_units(lo, hi, max_chars, 0)
    region = text[lo:hi]
    if sep not in region:
        return _recursive_units(text, lo, hi, max_chars, sep_index + 1)
    units: list[_Unit] = []
    cursor = lo
    parts = region.split(sep)
    for index, part in enumerate(parts):
        part_start = cursor
        part_end = cursor + len(part)
        if part:
            if part_end - part_start > max_chars:
                units.extend(_recursive_units(text, part_start, part_end, max_chars, sep_index + 1))
            else:
                units.append(_Unit(part_start, part_end))
        cursor = part_end
        if index < len(parts) - 1:
            cursor += len(sep)
    return units


def _chunks_from_units(
    document: ParsedDocument, units: list[_Unit], chunker: ChunkerName
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for unit in units:
        chunk = _make_chunk(document, unit.start, unit.end, chunker, unit.header_prefix)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def _pack(
    document: ParsedDocument,
    units: list[_Unit],
    *,
    size: int,
    overlap: int,
    chunker: ChunkerName,
) -> list[Chunk]:
    if not units:
        return []
    window = token_chars(size)
    chunks: list[Chunk] = []
    buf: list[_Unit] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        chunk = _make_chunk(document, buf[0].start, buf[-1].end, chunker)
        if chunk is not None:
            chunks.append(chunk)
        overlap_chars = token_chars(overlap) if overlap else 0
        buf = _overlap_tail(buf, overlap_chars)

    for unit in units:
        if unit.header_prefix:
            flush()
            buf = []
            chunk = _make_chunk(document, unit.start, unit.end, chunker, unit.header_prefix)
            if chunk is not None:
                chunks.append(chunk)
            continue
        if buf and (unit.end - buf[0].start) > window:
            flush()
        if not buf:
            buf = [unit]
        else:
            buf.append(unit)
    if buf:
        chunk = _make_chunk(document, buf[0].start, buf[-1].end, chunker)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def _overlap_tail(buf: list[_Unit], overlap_chars: int) -> list[_Unit]:
    if overlap_chars <= 0 or not buf:
        return []
    last_end = buf[-1].end
    kept: list[_Unit] = []
    for unit in reversed(buf):
        if unit.header_prefix:
            break
        kept.insert(0, unit)
        if last_end - unit.start >= overlap_chars:
            break
    return kept
