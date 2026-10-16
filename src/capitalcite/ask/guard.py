"""Refuse before the model when retrieval cannot support the question."""

from __future__ import annotations

import re
from collections.abc import Sequence

from capitalcite.retrieval.types import Hit

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_BANK_ALIASES = (
    ("jpmorgan chase", "JPM"),
    ("jp morgan chase", "JPM"),
    ("jpmorgan", "JPM"),
    ("jp morgan", "JPM"),
    ("wells fargo", "WFC"),
    ("bank of america", "BAC"),
    ("citigroup", "C"),
    ("citibank", "C"),
)


def retrieval_refusal(hits: Sequence[Hit], question: str, *, min_score: float) -> str | None:
    """Why these hits should not be sent to the model. None means they may be."""
    if not hits:
        return "no passages were retrieved"
    top = max(hit.score for hit in hits)
    if top < min_score:
        return "top hit score is below the retrieval floor"
    named = tickers_named(question)
    present = {hit.chunk.ticker.upper() for hit in hits if hit.chunk.ticker.strip()}
    if named and not named <= present:
        return "question names an entity that is not in the retrieved passages"
    years = years_named(question)
    hit_years = {hit.year for hit in hits if hit.year is not None}
    if years and not years <= hit_years:
        return "question names an entity that is not in the retrieved passages"
    return None


def tickers_named(question: str) -> set[str]:
    """Tickers and bank names the question mentions."""
    lowered = question.lower()
    found: set[str] = set()
    for needle, ticker in _BANK_ALIASES:
        if needle in lowered:
            found.add(ticker)
    for ticker in ("JPM", "BAC", "WFC"):
        if re.search(rf"\b{ticker}\b", question, flags=re.IGNORECASE):
            found.add(ticker)
    if re.search(r"\bciti(?:group|bank)?\b", question, flags=re.IGNORECASE):
        found.add("C")
    return found


def years_named(question: str) -> set[int]:
    """Four-digit years the question mentions."""
    return {int(match.group(0)) for match in _YEAR.finditer(question)}
