"""Load and validate the committed golden JSONL plus passage store."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from capitalcite.golden.models import GoldenItem, GoldenStats, ItemSource

DEFAULT_GOLDEN_DIR = Path("evals/golden")
ITEMS_NAME = "v1.jsonl"
PASSAGES_DIRNAME = "passages"
MIN_ITEMS = 90
UNANSWERABLE_TRICK_MIN = 0.15
NUMERIC_MIN = 0.30


class ValidationError(ValueError):
    """The golden set failed a schema, mix, or grounding check."""


def passage_filename(doc_id: str) -> str:
    return f"{doc_id.replace('/', '__')}.txt"


def load_passages(root: Path) -> dict[str, str]:
    directory = root / PASSAGES_DIRNAME
    if not directory.is_dir():
        raise ValidationError(f"missing passage directory: {directory}")
    passages: dict[str, str] = {}
    for path in sorted(directory.glob("*.txt")):
        passages[path.stem] = path.read_text(encoding="utf-8")
    if not passages:
        raise ValidationError(f"no passage files in {directory}")
    return passages


def load_items(path: Path) -> list[GoldenItem]:
    if not path.is_file():
        raise ValidationError(f"missing items file: {path}")
    items: list[GoldenItem] = []
    seen: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        item = GoldenItem.model_validate_json(raw)
        if item.id in seen:
            raise ValidationError(f"duplicate id {item.id} at {path}:{line_no}")
        seen.add(item.id)
        items.append(item)
    if not items:
        raise ValidationError(f"no items in {path}")
    return items


def stats_for(items: list[GoldenItem]) -> GoldenStats:
    n = len(items)
    types = Counter(item.type.value for item in items)
    sources = Counter(item.source.value for item in items)
    un_or_trick = sum(1 for item in items if item.is_unanswerable_or_trick)
    numeric = sum(1 for item in items if item.is_numeric)
    return GoldenStats(
        items=n,
        answerable=sum(1 for item in items if item.answerable),
        unanswerable_or_trick=un_or_trick,
        numeric=numeric,
        handwritten=sources.get(ItemSource.HANDWRITTEN.value, 0),
        financebench=sources.get(ItemSource.FINANCEBENCH.value, 0),
        finqa=sources.get(ItemSource.FINQA.value, 0),
        unanswerable_or_trick_pct=un_or_trick / n,
        numeric_pct=numeric / n,
        types=dict(sorted(types.items())),
        sources=dict(sorted(sources.items())),
    )


def ground_items(items: list[GoldenItem], passages: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for item in items:
        for row in item.evidence:
            key = passage_filename(row.doc_id)[:-4]
            text = passages.get(key)
            if text is None:
                errors.append(f"{item.id}: missing passage for doc_id={row.doc_id}")
                continue
            if row.quote not in text:
                errors.append(f"{item.id}: quote not found in passage {row.doc_id} ({row.section})")
            if f"## {row.section}" not in text:
                errors.append(
                    f"{item.id}: section heading {row.section!r} missing from {row.doc_id}"
                )
    return errors


def validate_mix(stats: GoldenStats) -> list[str]:
    errors: list[str] = []
    if stats.items < MIN_ITEMS:
        errors.append(f"need at least {MIN_ITEMS} items, have {stats.items}")
    if stats.unanswerable_or_trick_pct + 1e-12 < UNANSWERABLE_TRICK_MIN:
        errors.append(
            "unanswerable/trick share "
            f"{stats.unanswerable_or_trick_pct:.3f} < {UNANSWERABLE_TRICK_MIN}"
        )
    if stats.numeric_pct + 1e-12 < NUMERIC_MIN:
        errors.append(f"numeric share {stats.numeric_pct:.3f} < {NUMERIC_MIN}")
    if stats.financebench < 1 or stats.finqa < 1:
        errors.append("need at least one FinanceBench item and one FinQA item")
    if stats.handwritten < 1:
        errors.append("need handwritten items over the ingested filings")
    return errors


class GoldenSet:
    def __init__(self, items: list[GoldenItem], passages: dict[str, str], root: Path) -> None:
        self.items = items
        self.passages = passages
        self.root = root
        self.stats = stats_for(items)

    def validate(self) -> GoldenStats:
        errors = ground_items(self.items, self.passages)
        errors.extend(validate_mix(self.stats))
        if errors:
            raise ValidationError("\n".join(errors))
        return self.stats


def load_golden_set(root: Path | None = None) -> GoldenSet:
    directory = Path(root) if root is not None else DEFAULT_GOLDEN_DIR
    items = load_items(directory / ITEMS_NAME)
    passages = load_passages(directory)
    return GoldenSet(items=items, passages=passages, root=directory)


def validate_golden_set(root: Path | None = None) -> GoldenStats:
    return load_golden_set(root).validate()
