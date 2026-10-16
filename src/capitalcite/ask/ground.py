"""Expand a model draft into a citation, or refuse."""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from capitalcite.ask.types import Answer, Answered, Citation, ModelDraft, Refused, draft_problem
from capitalcite.retrieval.types import Hit

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)
_FIGURE = re.compile(
    r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|\$?\d+\.\d+%?"
    r"|\$?\d+%?"
)
_WORD_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")


def ground(draft: ModelDraft, hits: Sequence[Hit], question: str = "") -> Answer:
    """Copy doc_id, start, end, section, and source_url from the cited hit.

    Passage numbers are 1-based. The quote must be a verbatim substring of that
    hit's chunk text, and it must not be trivial. Every figure in the answer,
    other than years and figures already in the question, must appear in a
    cited quote. start and end stay the chunk span. quote_start and quote_end
    are the quote's document offsets. retrieval_score is the minimum retrieval
    score of the cited hits. A refusal is 0.
    """
    problem = draft_problem(draft)
    if problem is not None:
        return Refused(refusal_reason=problem)
    if draft.kind == "refused":
        return Refused(refusal_reason=draft.refusal_reason)
    citations: list[Citation] = []
    scores: list[float] = []
    quotes: list[str] = []
    for cite in draft.citations:
        if cite.index < 1 or cite.index > len(hits):
            return Refused(refusal_reason="citation index is outside the retrieved hits")
        hit = hits[cite.index - 1]
        chunk = hit.chunk
        if cite.quote == "" or cite.quote not in chunk.text:
            return Refused(
                refusal_reason="citation quote is not a verbatim substring of the cited passage"
            )
        if _trivial_quote(cite.quote):
            return Refused(refusal_reason="citation quote is trivial")
        quote_start, quote_end = _quote_span(
            chunk.text, cite.quote, chunk.start, chunk.header_prefix
        )
        citations.append(
            Citation(
                doc_id=chunk.doc_id,
                start=chunk.start,
                end=chunk.end,
                quote=cite.quote,
                quote_start=quote_start,
                quote_end=quote_end,
                section=chunk.section,
                source_url=chunk.source_url,
            )
        )
        scores.append(hit.score)
        quotes.append(cite.quote)
    missing = _ungrounded_figures(draft.answer, question, quotes)
    if missing:
        return Refused(refusal_reason="answer contains a figure not present in the cited quotes")
    return Answered(
        answer=draft.answer,
        citations=tuple(citations),
        retrieval_score=min(scores),
    )


def _trivial_quote(quote: str) -> bool:
    stripped = quote.strip()
    if not stripped:
        return True
    words = [_WORD_PUNCT.sub("", part) for part in stripped.split()]
    words = [word for word in words if word]
    content = any(word.lower() not in _STOPWORDS for word in words)
    return not (len(stripped) >= 20 or (len(words) >= 3 and content))


def _quote_span(text: str, quote: str, chunk_start: int, header_prefix: str) -> tuple[int, int]:
    pos = text.find(quote)
    prefix_len = len(header_prefix)
    skip = max(0, prefix_len - pos)
    quote_start = chunk_start + max(0, pos - prefix_len)
    quote_end = quote_start + len(quote) - skip
    return quote_start, quote_end


def _ungrounded_figures(answer: str, question: str, quotes: Sequence[str]) -> set[str]:
    allowed = figures_in(question)
    cited: set[str] = set()
    for quote in quotes:
        cited.update(figures_in(quote))
    return figures_in(answer) - allowed - cited


def figures_in(text: str) -> set[str]:
    """Normalized non-year figures in text."""
    found: set[str] = set()
    for match in _FIGURE.finditer(text):
        token = match.group(0)
        normalized = normalize_figure(token)
        if not normalized or _is_year(normalized, token):
            continue
        found.add(normalized)
    return found


def normalize_figure(token: str) -> str:
    stripped = token.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        value = Decimal(stripped)
    except InvalidOperation:
        return stripped
    if value == 0:
        return "0"
    rendered = format(value.normalize(), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _is_year(normalized: str, raw: str) -> bool:
    if "$" in raw or "%" in raw:
        return False
    if not normalized.isdigit() or len(normalized) != 4:
        return False
    year = int(normalized)
    return 1900 <= year <= 2099
