from capitalcite.config import Settings
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


def open_store(settings: Settings) -> GraphStore | None:
    uri = settings.neo4j_uri
    if uri is None or not uri.strip():
        return None
    return Neo4jGraphStore(uri.strip(), settings.neo4j_user, settings.neo4j_password or "")
