"""Build one Qdrant collection and search it."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from qdrant_client.models import (
    CollectionInfo,
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    Modifier,
    PayloadSchemaType,
    PointStruct,
    Prefetch,
    Rrf,
    RrfQuery,
    ScoredPoint,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from capitalcite.atomic import atomic_write_text
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.config import Settings
from capitalcite.retrieval.embedders import build_embedder
from capitalcite.retrieval.hashing import collection_name, config_hash, point_id, year_of
from capitalcite.retrieval.types import (
    CorruptChunks,
    Embedder,
    EmbedderName,
    ExactPeriod,
    Hit,
    IndexConfig,
    IndexNotBuilt,
    IndexReceipt,
    IndexUnavailable,
    RetrievalError,
    RetrievalMode,
    SearchFilters,
    TextEmbedding,
    YearPeriod,
)

logger = logging.getLogger(__name__)

_UPSERT_BATCH = 64
_SCROLL_BATCH = 256
_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")


class RetrievalSetup:
    """One config, the embedder that matches it, and a hidden client."""

    def __init__(
        self,
        config: IndexConfig,
        embedder: Embedder,
        client: AsyncQdrantClient,
        *,
        rrf_k: int,
        search_k: int,
    ) -> None:
        if embedder.id != config.embedder_id or embedder.dense_size != config.dense_size:
            raise ValueError("embedder does not match index config")
        self.config = config
        self.embedder = embedder
        self._client = client
        self.rrf_k = rrf_k
        self.search_k = search_k

    async def aclose(self) -> None:
        await self._client.close()


_PAYLOAD_INDEXES: tuple[tuple[str, PayloadSchemaType], ...] = (
    ("ticker", PayloadSchemaType.KEYWORD),
    ("form", PayloadSchemaType.KEYWORD),
    ("period", PayloadSchemaType.KEYWORD),
    ("year", PayloadSchemaType.INTEGER),
)


def setup_retrieval(
    settings: Settings,
    *,
    client: AsyncQdrantClient,
    embedder: Embedder,
) -> RetrievalSetup:
    """Build IndexConfig from settings and this embedder. No network I/O."""
    if settings.chunk_overlap >= settings.chunk_size:
        raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
    if _PREFIX.fullmatch(settings.qdrant_collection) is None:
        raise ValueError("QDRANT_COLLECTION must be a Qdrant collection name token")
    config = IndexConfig(
        embedder_id=embedder.id,
        dense_size=embedder.dense_size,
        chunker=settings.chunker,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        collection_prefix=settings.qdrant_collection,
    )
    return RetrievalSetup(
        config,
        embedder,
        client,
        rrf_k=settings.rrf_k,
        search_k=settings.search_k,
    )


def open_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """Open the store Settings names. Path and :memory: never dial a URL.

    QDRANT_PATH wins over QDRANT_URL. Neither location is part of IndexConfig.
    """
    # Compatibility check starts a daemon thread that dials a remote during
    # construction. A down server has to fail the search RPC instead.
    if settings.qdrant_path is not None:
        return AsyncQdrantClient(
            path=str(settings.qdrant_path),
            timeout=5,
            check_compatibility=False,
        )
    if settings.qdrant_url == ":memory:":
        return AsyncQdrantClient(
            location=":memory:",
            timeout=5,
            check_compatibility=False,
        )
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=5,
        check_compatibility=False,
    )


def create_retrieval_setup(settings: Settings) -> RetrievalSetup:
    """Production constructor. Does not load FastEmbed. Embedded stores open locally."""
    embedder = build_embedder(EmbedderName(settings.embedder), settings.openai_api_key)
    return setup_retrieval(
        settings,
        client=open_qdrant_client(settings),
        embedder=embedder,
    )


async def build_index(setup: RetrievalSetup, input_dir: Path) -> IndexReceipt:
    """Index parsed/edgar/**/chunks.jsonl under input_dir.

    Upserts by point id, deletes ids this walk did not write, then writes the
    sidecar. A crash before the sidecar leaves a collection the next run repairs.
    """
    chunks = _read_chunks(input_dir, setup.config.chunker)
    name = collection_name(setup.config)
    client = setup._client
    await _ensure_collection(client, setup.config, name)
    by_id: dict[str, Chunk] = {}
    for chunk in chunks:
        pid = point_id(chunk.doc_id, chunk.chunker.value, chunk.start, chunk.end)
        by_id[pid] = chunk
    ordered = list(by_id.items())
    embeddings = await _embed_batched(setup.embedder, [chunk.text for _, chunk in ordered])
    points = [
        _point(pid, chunk, embedding, setup.config)
        for (pid, chunk), embedding in zip(ordered, embeddings, strict=True)
    ]
    for batch in _batches(points, _UPSERT_BATCH):
        await _call(client.upsert(collection_name=name, points=list(batch)), collection=name)
    await _delete_orphans(client, name, set(by_id))
    counted = await _call(client.count(collection_name=name, exact=True), collection=name)
    total = int(counted.count)
    if total != len(by_id):
        raise RetrievalError(
            f"collection {name} has {total} points after rebuild, expected {len(by_id)}"
        )
    digest = config_hash(setup.config)
    receipt = IndexReceipt(
        config_hash=digest,
        collection_name=name,
        embedder_id=setup.config.embedder_id,
        point_count=total,
        canonical=setup.config,
    )
    atomic_write_text(
        input_dir / "index" / f"{name}.json",
        receipt.model_dump_json(indent=2) + "\n",
    )
    logger.info("indexed collection=%s points=%s config_hash=%s", name, total, digest)
    return receipt


async def point_count(setup: RetrievalSetup) -> int:
    name = collection_name(setup.config)
    if not await _collection_ready(setup._client, name):
        raise IndexNotBuilt(name)
    counted = await _call(setup._client.count(collection_name=name, exact=True), collection=name)
    return int(counted.count)


async def retrieve(
    setup: RetrievalSetup,
    query: str,
    *,
    mode: RetrievalMode = RetrievalMode.HYBRID,
    k: int | None = None,
    filters: SearchFilters | None = None,
) -> list[Hit]:
    """Run one query. Does not write the collection or the sidecar."""
    if not query.strip():
        raise ValueError("query is empty")
    limit = setup.search_k if k is None else k
    if limit < 1:
        raise ValueError("k must be at least 1")
    name = collection_name(setup.config)
    client = setup._client
    if not await _collection_ready(client, name):
        raise IndexNotBuilt(name)
    embedding = await setup.embedder.embed_query(query)
    if len(embedding.dense) != setup.config.dense_size:
        width = len(embedding.dense)
        raise RetrievalError(
            f"embedder returned {width} dims, config expects {setup.config.dense_size}"
        )
    qfilter = _payload_filter(filters)
    prefetch_limit = max(limit * 4, 20)
    dense_name = setup.config.dense_vector
    sparse_name = setup.config.sparse_vector
    dense_query = list(embedding.dense)
    sparse_query = _sparse(embedding)
    if mode is RetrievalMode.DENSE:
        response = await _call(
            client.query_points(
                collection_name=name,
                query=dense_query,
                using=dense_name,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            ),
            collection=name,
        )
    elif mode is RetrievalMode.BM25:
        response = await _call(
            client.query_points(
                collection_name=name,
                query=sparse_query,
                using=sparse_name,
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            ),
            collection=name,
        )
    elif mode is RetrievalMode.HYBRID:
        response = await _call(
            client.query_points(
                collection_name=name,
                prefetch=[
                    Prefetch(
                        query=dense_query,
                        using=dense_name,
                        filter=qfilter,
                        limit=prefetch_limit,
                    ),
                    Prefetch(
                        query=sparse_query,
                        using=sparse_name,
                        filter=qfilter,
                        limit=prefetch_limit,
                    ),
                ],
                query=RrfQuery(rrf=Rrf(k=setup.rrf_k)),
                query_filter=qfilter,
                limit=limit,
                with_payload=True,
            ),
            collection=name,
        )
    else:
        assert_never(mode)
    return _hits(response.points)[:limit]


def _read_chunks(input_dir: Path, expected: ChunkerName) -> list[Chunk]:
    root = input_dir / "parsed" / "edgar"
    if not root.is_dir():
        return []
    chunks: list[Chunk] = []
    for path in sorted(root.glob("**/chunks.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                chunk = Chunk.model_validate_json(line)
            except ValidationError as exc:
                raise CorruptChunks(f"{path}:{line_no}: {exc}") from exc
            if chunk.chunker is not expected:
                raise CorruptChunks(f"{path}:{line_no}: chunker {chunk.chunker} != {expected}")
            chunks.append(chunk)
    return chunks


def _point(
    pid: str,
    chunk: Chunk,
    embedding: TextEmbedding,
    config: IndexConfig,
) -> PointStruct:
    if len(embedding.dense) == 0:
        raise RetrievalError("embedder returned an empty dense vector")
    payload = chunk.model_dump(mode="json")
    payload["year"] = year_of(chunk.period)
    return PointStruct(
        id=pid,
        vector={
            config.dense_vector: list(embedding.dense),
            config.sparse_vector: _sparse(embedding),
        },
        payload=payload,
    )


def _sparse(embedding: TextEmbedding) -> SparseVector:
    return SparseVector(
        indices=list(embedding.sparse.indices),
        values=list(embedding.sparse.values),
    )


async def _embed_batched(embedder: Embedder, texts: list[str]) -> list[TextEmbedding]:
    if not texts:
        return []
    embeddings: list[TextEmbedding] = []
    for batch in _batches(texts, _UPSERT_BATCH):
        vectors = await embedder.embed_documents(list(batch))
        if len(vectors) != len(batch):
            raise RetrievalError("embedder returned a different number of vectors than texts")
        embeddings.extend(vectors)
    return embeddings


async def _ensure_collection(
    client: AsyncQdrantClient,
    config: IndexConfig,
    name: str,
) -> None:
    if await _collection_ready(client, name):
        info = await _call(client.get_collection(name), collection=name)
        _require_schema(info, config)
        return
    await _call(
        client.create_collection(
            collection_name=name,
            vectors_config={
                config.dense_vector: VectorParams(size=config.dense_size, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                config.sparse_vector: SparseVectorParams(modifier=Modifier.IDF),
            },
        ),
        collection=name,
    )
    for field_name, schema in _PAYLOAD_INDEXES:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Payload indexes have no effect in the local Qdrant.*",
                category=UserWarning,
            )
            await _call(
                client.create_payload_index(
                    collection_name=name,
                    field_name=field_name,
                    field_schema=schema,
                ),
                collection=name,
            )


def _require_schema(info: CollectionInfo, config: IndexConfig) -> None:
    vectors = info.config.params.vectors
    dense_params: VectorParams | None
    if isinstance(vectors, dict):
        dense_params = vectors.get(config.dense_vector)
    elif isinstance(vectors, VectorParams):
        dense_params = vectors
    else:
        dense_params = None
    if dense_params is None or dense_params.size != config.dense_size:
        raise RetrievalError(f"collection dense schema does not match {config.embedder_id}")
    if dense_params.distance != Distance.COSINE:
        raise RetrievalError("collection dense distance is not cosine")
    sparse = info.config.params.sparse_vectors or {}
    sparse_params = sparse.get(config.sparse_vector)
    if sparse_params is None or sparse_params.modifier != Modifier.IDF:
        raise RetrievalError("collection sparse schema is not bm25 with IDF")


def _payload_filter(filters: SearchFilters | None) -> Filter | None:
    if filters is None:
        return None
    must: list[FieldCondition] = []
    if filters.ticker is not None:
        must.append(FieldCondition(key="ticker", match=MatchValue(value=filters.ticker)))
    if filters.form is not None:
        must.append(FieldCondition(key="form", match=MatchValue(value=filters.form)))
    period = filters.period
    if isinstance(period, ExactPeriod):
        must.append(FieldCondition(key="period", match=MatchValue(value=period.period)))
    elif isinstance(period, YearPeriod):
        must.append(FieldCondition(key="year", match=MatchValue(value=period.year)))
    if not must:
        return None
    # Filter.must is typed as an invariant union list. FieldCondition is one member.
    return Filter(must=must)  # type: ignore[arg-type]


def _hits(points: Sequence[ScoredPoint]) -> list[Hit]:
    hits: list[Hit] = []
    for point in points:
        payload = point.payload or {}
        if not isinstance(payload, dict):
            continue
        raw = dict(payload)
        raw.pop("year", None)
        try:
            chunk = Chunk.model_validate(raw)
        except ValidationError:
            continue
        hits.append(Hit(score=float(point.score or 0.0), chunk=chunk))
    return hits


async def _delete_orphans(
    client: AsyncQdrantClient,
    name: str,
    kept: set[str],
) -> None:
    seen: set[str] = set()
    offset = None
    while True:
        records, offset = await _call(
            client.scroll(
                collection_name=name,
                limit=_SCROLL_BATCH,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            ),
            collection=name,
        )
        if not records:
            break
        for record in records:
            seen.add(str(record.id))
        if offset is None:
            break
    stale = [pid for pid in seen if pid not in kept]
    for batch in _batches(stale, _SCROLL_BATCH):
        await _call(
            client.delete(collection_name=name, points_selector=list(batch)),
            collection=name,
        )


async def _collection_ready(client: AsyncQdrantClient, name: str) -> bool:
    try:
        return bool(await client.collection_exists(name))
    except UnexpectedResponse as exc:
        if exc.status_code == 404:
            return False
        raise IndexUnavailable(str(exc)) from exc
    except ResponseHandlingException as exc:
        raise IndexUnavailable(str(exc)) from exc


async def _call[T](awaitable: Awaitable[T], *, collection: str) -> T:
    try:
        return await awaitable
    except UnexpectedResponse as exc:
        if exc.status_code == 404:
            raise IndexNotBuilt(collection) from exc
        raise IndexUnavailable(str(exc)) from exc
    except ResponseHandlingException as exc:
        raise IndexUnavailable(str(exc)) from exc


def _batches[T](items: Sequence[T], size: int) -> list[Sequence[T]]:
    return [items[start : start + size] for start in range(0, len(items), size)]
