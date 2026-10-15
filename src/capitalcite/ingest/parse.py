"""CLI argument parsing for ingest."""

from __future__ import annotations


class ParseError(ValueError):
    """User-facing ingest flag error."""


def parse_tickers(value: str) -> tuple[str, ...]:
    """Split comma-separated tickers and normalize to uppercase."""
    parts = tuple(dict.fromkeys(p.strip().upper() for p in value.split(",") if p.strip()))
    if not parts:
        raise ParseError("--tickers needs at least one ticker")
    for ticker in parts:
        if not ticker.isalnum():
            raise ParseError(f"invalid ticker {ticker!r}")
    return parts


def parse_forms(value: str) -> tuple[str, ...]:
    """Split comma-separated SEC form names (e.g. 10-K, 10-Q)."""
    parts = tuple(dict.fromkeys(p.strip() for p in value.split(",") if p.strip()))
    if not parts:
        raise ParseError("--forms needs at least one form, e.g. 10-K")
    return parts


def parse_years(value: str) -> frozenset[int]:
    """Parse a year, comma list, or inclusive range such as 2023-2025."""
    stripped = value.strip()
    if not stripped:
        raise ParseError("--years needs a year or range, e.g. 2023-2025")

    if "," not in stripped and stripped.count("-") == 1:
        left, right = stripped.split("-", 1)
        if left.strip().isdigit() and right.strip().isdigit():
            start, end = int(left), int(right)
            _check_year(start)
            _check_year(end)
            if start > end:
                raise ParseError(f"year range start {start} is after end {end}")
            return frozenset(range(start, end + 1))

    years: list[int] = []
    for part in stripped.split(","):
        token = part.strip()
        if not token.isdigit():
            raise ParseError(f"invalid year {token!r} in {value!r}")
        year = int(token)
        _check_year(year)
        if year not in years:
            years.append(year)
    if not years:
        raise ParseError("--years needs a year or range, e.g. 2023-2025")
    return frozenset(years)


def _check_year(year: int) -> None:
    if year < 1990 or year > 2100:
        raise ParseError(f"year {year} is outside 1990-2100")
