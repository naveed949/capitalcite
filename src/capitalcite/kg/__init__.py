"""Optional entity graph. Memory is the test store. Neo4j is optional Bolt."""

from capitalcite.kg.memory import MemoryGraphStore
from capitalcite.kg.neo4j_store import Neo4jGraphStore
from capitalcite.kg.snapshot import DEFAULT_SNAPSHOT, load_snapshot
from capitalcite.kg.types import (
    Bank,
    FilingNode,
    GraphHit,
    GraphQuery,
    GraphQueryKind,
    GraphResult,
    GraphSnapshot,
    GraphStore,
    LoadReceipt,
    MetricNode,
    ParentOf,
    render_hits,
)

__all__ = [
    "DEFAULT_SNAPSHOT",
    "Bank",
    "FilingNode",
    "GraphHit",
    "GraphQuery",
    "GraphQueryKind",
    "GraphResult",
    "GraphSnapshot",
    "GraphStore",
    "LoadReceipt",
    "MemoryGraphStore",
    "MetricNode",
    "Neo4jGraphStore",
    "ParentOf",
    "load_snapshot",
    "open_store",
    "render_hits",
]


def open_store(
    uri: str | None, user: str = "neo4j", password: str | None = None
) -> GraphStore | None:
    """None when URI is unset. Callers inject MemoryGraphStore in tests."""
    if uri is None or not uri.strip():
        return None
    return Neo4jGraphStore(uri.strip(), user, password or "")
