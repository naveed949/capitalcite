"""Golden Q&A set: typed items, passage store, and validation."""

from capitalcite.golden.models import (
    Evidence,
    EvidenceRole,
    GoldenItem,
    GoldenStats,
    ItemSource,
    QuestionType,
)
from capitalcite.golden.raw import validate_against_raw
from capitalcite.golden.store import (
    DEFAULT_GOLDEN_DIR,
    GoldenSet,
    ValidationError,
    load_golden_set,
    validate_golden_set,
)

__all__ = [
    "DEFAULT_GOLDEN_DIR",
    "Evidence",
    "EvidenceRole",
    "GoldenItem",
    "GoldenSet",
    "GoldenStats",
    "ItemSource",
    "QuestionType",
    "ValidationError",
    "load_golden_set",
    "validate_against_raw",
    "validate_golden_set",
]
