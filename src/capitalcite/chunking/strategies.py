"""Fixed, recursive, and section-aware splitters."""

from __future__ import annotations

from collections.abc import Callable

from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.tokens import count_tokens, token_chars
from capitalcite.parse.models import ParsedDocument
from capitalcite.parse.sections import page_at, section_at

_SEPARATORS = ("\n\n", "\n", ". ", " ", "")


def chunk_fixed(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    """Sliding windows of ``size`` tokens with ``overlap`` tokens of shared tail."""
    _check_budgets(size, overlap)
    text = document.text
    if not text.strip():
        return []
    window = token_chars(size)
    stride = token_chars(size - overlap)
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        end = min(start + window, len(text))
        end = _avoid_table_cut(text, start, end)
        piece = text[start:end].strip()
        if piece:
            chunks.append(_make_chunk(document, piece, start, ChunkerName.FIXED))
        if end >= len(text):
            break
        start = max(start + stride, end - token_chars(overlap))
        if start >= end:
            start = end
    return chunks


def chunk_recursive(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    """Split on large separators first, then pack pieces up to ``size`` tokens."""
    _check_budgets(size, overlap)
    pieces = _recursive_split(document.text, token_chars(size))
    return _pack(document, pieces, size=size, overlap=overlap, chunker=ChunkerName.RECURSIVE)


def chunk_section(document: ParsedDocument, *, size: int, overlap: int) -> list[Chunk]:
    """One chunk per Item when it fits. Oversized items use the recursive splitter."""
    _check_budgets(size, overlap)
    chunks: list[Chunk] = []
    spans = document.sections
    if not spans:
        return chunk_recursive(document, size=size, overlap=overlap)
    for span in spans:
        piece = document.text[span.start : span.end]
        if not piece.strip():
            continue
        if count_tokens(piece) <= size:
            chunks.append(_make_chunk(document, piece.strip(), span.start, ChunkerName.SECTION))
            continue
        nested = ParsedDocument(
            source=document.source,
            text=piece,
            media_type=document.media_type,
            sections=(),
            pages=tuple(
                p.model_copy(
                    update={
                        "start": max(0, p.start - span.start),
                        "end": max(0, p.end - span.start),
                    }
                )
                for p in document.pages
            ),
            tables=document.tables,
        )
        for child in chunk_recursive(nested, size=size, overlap=overlap):
            chunks.append(
                child.model_copy(
                    update={
                        "chunker": ChunkerName.SECTION,
                        "section": span.name,
                        "offset": span.start + child.offset,
                        "page": page_at(document.pages, span.start + child.offset),
                    }
                )
            )
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


def _make_chunk(document: ParsedDocument, text: str, offset: int, chunker: ChunkerName) -> Chunk:
    source = document.source
    return Chunk(
        doc_id=source.doc_id,
        section=section_at(document.sections, offset),
        offset=offset,
        source_url=source.source_url,
        text=text,
        token_count=count_tokens(text),
        chunker=chunker,
        page=page_at(document.pages, offset),
        ticker=source.ticker,
        form=source.form,
        period=source.period,
        accession=source.accession,
    )


def _avoid_table_cut(text: str, start: int, end: int) -> int:
    """If ``end`` falls inside a markdown table, snap to the table boundary."""
    table_start = text.rfind("\n\n|", start, end)
    if table_start == -1 and text.startswith("|", start):
        table_start = start
    if table_start == -1:
        return end
    table_end = text.find("\n\n", table_start + 2)
    if table_end == -1:
        table_end = len(text)
    if start < table_start < end < table_end:
        return table_start
    if table_start <= start < table_end and end < table_end:
        return table_end
    return end


def _recursive_split(text: str, max_chars: int) -> list[tuple[int, str]]:
    stripped = text
    if not stripped:
        return []
    if len(stripped) <= max_chars:
        return [(0, stripped)]
    return _split_from(stripped, 0, max_chars, 0)


def _split_from(text: str, origin: int, max_chars: int, sep_index: int) -> list[tuple[int, str]]:
    if len(text) <= max_chars or sep_index >= len(_SEPARATORS):
        return [(origin, text)] if text.strip() else []
    sep = _SEPARATORS[sep_index]
    if sep == "":
        pieces: list[tuple[int, str]] = []
        for index in range(0, len(text), max_chars):
            piece = text[index : index + max_chars]
            if piece.strip():
                pieces.append((origin + index, piece))
        return pieces
    parts = text.split(sep)
    if len(parts) == 1:
        return _split_from(text, origin, max_chars, sep_index + 1)
    out: list[tuple[int, str]] = []
    cursor = origin
    for index, part in enumerate(parts):
        if part:
            if len(part) > max_chars:
                out.extend(_split_from(part, cursor, max_chars, sep_index + 1))
            else:
                out.append((cursor, part))
        cursor += len(part)
        if index < len(parts) - 1:
            cursor += len(sep)
    return out


def _pack(
    document: ParsedDocument,
    pieces: list[tuple[int, str]],
    *,
    size: int,
    overlap: int,
    chunker: ChunkerName,
) -> list[Chunk]:
    if not pieces:
        return []
    window = token_chars(size)
    chunks: list[Chunk] = []
    buf: list[tuple[int, str]] = []
    buf_len = 0
    for offset, piece in pieces:
        piece_len = len(piece)
        extra = (2 if buf else 0) + piece_len
        if buf and buf_len + extra > window:
            text = "\n\n".join(part for _, part in buf).strip()
            if text:
                chunks.append(_make_chunk(document, text, buf[0][0], chunker))
            overlap_chars = token_chars(overlap) if overlap else 0
            buf, buf_len = _overlap_tail(buf, overlap_chars)
        if not buf:
            buf = [(offset, piece)]
            buf_len = piece_len
        else:
            buf.append((offset, piece))
            buf_len += extra
    if buf:
        text = "\n\n".join(part for _, part in buf).strip()
        if text:
            chunks.append(_make_chunk(document, text, buf[0][0], chunker))
    return chunks


def _overlap_tail(
    buf: list[tuple[int, str]], overlap_chars: int
) -> tuple[list[tuple[int, str]], int]:
    if overlap_chars <= 0 or not buf:
        return [], 0
    kept: list[tuple[int, str]] = []
    total = 0
    for offset, piece in reversed(buf):
        kept.insert(0, (offset, piece))
        total += len(piece)
        if total >= overlap_chars:
            break
    return kept, total
