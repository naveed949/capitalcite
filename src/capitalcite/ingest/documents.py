"""Choose the real 10-K HTML from an EDGAR filing index."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_EXHIBIT = re.compile(r"(exhibit|dex\d|xex\d|(^|[^a-z0-9])ex\d)", re.IGNORECASE)
_RENDER = re.compile(r"^R\d+\.html?$", re.IGNORECASE)
_INDEX = re.compile(r"(^|-)index\.html?$", re.IGNORECASE)


@dataclass(frozen=True)
class IndexItem:
    name: str
    size: int


def parse_filing_index(payload: object) -> list[IndexItem]:
    if not isinstance(payload, dict):
        return []
    directory = payload.get("directory")
    if not isinstance(directory, dict):
        return []
    raw = directory.get("item")
    if isinstance(raw, dict):
        rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        return []
    items: list[IndexItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).rsplit("/", 1)[-1]
        if not name or name in {".", ".."}:
            continue
        try:
            size = int(str(row.get("size") or 0))
        except ValueError:
            size = 0
        items.append(IndexItem(name=name, size=max(size, 0)))
    return items


def is_main_10k_html(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    lowered = base.lower()
    if not lowered.endswith((".htm", ".html")):
        return False
    if _RENDER.match(base) or _INDEX.search(lowered):
        return False
    return not _EXHIBIT.search(lowered)


def choose_main_document(declared: str, items: Sequence[IndexItem]) -> str:
    """Return the largest main 10-K HTML when it beats the declared primary."""
    declared_name = declared.rsplit("/", 1)[-1]
    candidates = [item for item in items if is_main_10k_html(item.name)]
    if not candidates:
        return declared_name
    best = max(candidates, key=lambda item: (item.size, item.name))
    declared_size = next((item.size for item in items if item.name == declared_name), -1)
    if best.size > declared_size:
        return best.name
    return declared_name
