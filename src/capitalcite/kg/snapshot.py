"""Load a committed GraphSnapshot JSON object."""

from __future__ import annotations

from pathlib import Path

from capitalcite.kg.types import GraphSnapshot

DEFAULT_SNAPSHOT = Path("evals/golden/graph_snapshot.json")


def load_snapshot(path: Path | None = None) -> GraphSnapshot:
    target = path if path is not None else DEFAULT_SNAPSHOT
    return GraphSnapshot.model_validate_json(target.read_text(encoding="utf-8"))
