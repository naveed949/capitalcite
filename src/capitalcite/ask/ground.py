"""Expand a model draft into a citation, or refuse."""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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
_NUMBER = (
    r"\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?"
    r"|\$?\d+\.\d+%?"
    r"|\$?\d+%?"
)
_SCALE = r"thousands?|millions?|billions?|bn"
_FIGURE = re.compile(
    rf"(?P<num>{_NUMBER})(?:\s*(?P<scale>{_SCALE})\b|(?P<suffix>[kKmMbB])(?![A-Za-z]))?"
)
_WORD_PUNCT = re.compile(r"^[\W_]+|[\W_]+$")
_SCALES: dict[str, Decimal] = {
    "k": Decimal("1000"),
    "thousand": Decimal("1000"),
    "thousands": Decimal("1000"),
    "m": Decimal("1000000"),
    "million": Decimal("1000000"),
    "millions": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "bn": Decimal("1000000000"),
    "billion": Decimal("1000000000"),
    "billions": Decimal("1000000000"),
}


def ground(draft: ModelDraft, hits: Sequence[Hit], question: str = "") -> Answer:
    """Copy doc_id, start, end, section, and source_url from the cited hit.

    Passage numbers are 1-based. The quote must be a verbatim substring of that
    hit's chunk text, and it must not be trivial. Every figure in the answer,
    other than years and figures already in the question, must appear in a
    cited quote. A scale word on the answer (billion, million, thousand) may
    match a bare table integer when the integer times that scale rounds to the
    quoted precision. start and end stay the chunk span. quote_start and
    quote_end are the quote's document offsets. retrieval_score is the minimum
    retrieval score of the cited hits. A refusal is 0.
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
    allowed = _parse_figures(question)
    cited: list[_Figure] = []
    for quote in quotes:
        cited.extend(_parse_figures(quote))
    missing: set[str] = set()
    for figure in _parse_figures(answer):
        if _grounded_figure(figure, allowed, cited):
            continue
        missing.add(figure.normalized)
    return missing


def figures_in(text: str) -> set[str]:
    """Normalized non-year figures in text, ignoring scale words."""
    return {figure.normalized for figure in _parse_figures(text)}


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


class _Figure:
    __slots__ = ("decimals", "normalized", "percent", "raw", "scale", "value")

    def __init__(
        self,
        *,
        normalized: str,
        raw: str,
        scale: Decimal,
        value: Decimal | None,
        percent: bool,
        decimals: int,
    ) -> None:
        self.normalized = normalized
        self.raw = raw
        self.scale = scale
        self.value = value
        self.percent = percent
        self.decimals = decimals


def _parse_figures(text: str) -> list[_Figure]:
    found: list[_Figure] = []
    for match in _FIGURE.finditer(text):
        raw_num = match.group("num")
        scale_word = match.group("scale") or match.group("suffix") or ""
        normalized = normalize_figure(raw_num)
        if not normalized or _is_year(normalized, raw_num):
            continue
        value: Decimal | None
        try:
            value = Decimal(raw_num.replace(",", "").replace("$", "").replace("%", ""))
        except InvalidOperation:
            value = None
        found.append(
            _Figure(
                normalized=normalized,
                raw=raw_num,
                scale=_scale_of(scale_word),
                value=value,
                percent="%" in raw_num,
                decimals=_decimal_places(raw_num),
            )
        )
    return found


def _grounded_figure(figure: _Figure, allowed: Sequence[_Figure], cited: Sequence[_Figure]) -> bool:
    if any(_same_token(figure, other) for other in allowed):
        return True
    if any(_same_token(figure, other) for other in cited):
        return True
    if figure.percent:
        return False
    return any(_same_amount(figure, other) for other in cited if not other.percent)


def _same_token(left: _Figure, right: _Figure) -> bool:
    return left.normalized == right.normalized and left.percent == right.percent


def _same_amount(answer: _Figure, cited: _Figure) -> bool:
    target = _canonical(answer)
    if target is None or cited.value is None:
        return False
    quantum = _quantum(answer)
    for scale in _candidate_scales(cited):
        source = cited.value * scale
        if _rounds_to(source, target, quantum):
            return True
    return False


def _canonical(figure: _Figure) -> Decimal | None:
    if figure.value is None:
        return None
    return figure.value * figure.scale


def _quantum(figure: _Figure) -> Decimal:
    if figure.decimals <= 0:
        return figure.scale
    return figure.scale * (Decimal(10) ** -figure.decimals)


def _rounds_to(source: Decimal, target: Decimal, quantum: Decimal) -> bool:
    if quantum == 0:
        return source == target
    rounded = (source / quantum).to_integral_value(rounding=ROUND_HALF_UP) * quantum
    return rounded == target


def _candidate_scales(cited: _Figure) -> tuple[Decimal, ...]:
    if cited.scale != 1:
        return (cited.scale,)
    if cited.value is None or cited.percent:
        return (Decimal(1),)
    if cited.value != cited.value.to_integral_value():
        return (Decimal(1),)
    digits = len(cited.normalized.lstrip("-").split(".", 1)[0])
    if digits < 4:
        return (Decimal(1),)
    return (Decimal(1), Decimal("1000"), Decimal("1000000"), Decimal("1000000000"))


def _scale_of(word: str) -> Decimal:
    if not word:
        return Decimal(1)
    return _SCALES.get(word.lower(), Decimal(1))


def _decimal_places(raw: str) -> int:
    stripped = raw.replace(",", "").replace("$", "").replace("%", "")
    if "." not in stripped:
        return 0
    return len(stripped.split(".", 1)[1])


def _is_year(normalized: str, raw: str) -> bool:
    if "$" in raw or "%" in raw:
        return False
    if not normalized.isdigit() or len(normalized) != 4:
        return False
    year = int(normalized)
    return 1900 <= year <= 2099
