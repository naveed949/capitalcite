"""Dense embedders plus a BM25 sparse encoder. FastEmbed Qdrant/bm25 in production."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import threading
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from capitalcite.retrieval.hashing import tokens
from capitalcite.retrieval.sparse import (
    FAKE_SPARSE_ID,
    FASTEMBED_SPARSE_ID,
    SPARSE_MODEL,
    bm25_document,
    bm25_query,
    bm25_terms,
)
from capitalcite.retrieval.types import (
    Embedder,
    EmbedderName,
    EmbedderNotConfigured,
    RetrievalError,
    SparseTermVector,
    TextEmbedding,
)

if TYPE_CHECKING:
    from fastembed import TextEmbedding as FastTextEmbedding
    from fastembed.sparse import SparseTextEmbedding as FastSparseEmbedding


class EmbedderSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: EmbedderName
    embedder_id: str
    dense_size: int
    kind: Literal["fastembed", "openai"]
    model_name: str
    query_prefix: str = ""


# BGE was trained with this query instruction. It is applied only to the dense
# query so the words never enter the sparse vector.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

EMBEDDER_SPECS: dict[EmbedderName, EmbedderSpec] = {
    EmbedderName.BGE_SMALL: EmbedderSpec(
        name=EmbedderName.BGE_SMALL,
        embedder_id="fastembed:BAAI/bge-small-en-v1.5",
        dense_size=384,
        kind="fastembed",
        model_name="BAAI/bge-small-en-v1.5",
        query_prefix=_BGE_QUERY_PREFIX,
    ),
    EmbedderName.MINILM: EmbedderSpec(
        name=EmbedderName.MINILM,
        embedder_id="fastembed:sentence-transformers/all-MiniLM-L6-v2",
        dense_size=384,
        kind="fastembed",
        model_name="sentence-transformers/all-MiniLM-L6-v2",
    ),
    EmbedderName.OPENAI_SMALL: EmbedderSpec(
        name=EmbedderName.OPENAI_SMALL,
        embedder_id="openai:text-embedding-3-small",
        dense_size=1536,
        kind="openai",
        model_name="text-embedding-3-small",
    ),
}


def spec_for(name: EmbedderName) -> EmbedderSpec:
    return EMBEDDER_SPECS[name]


class FakeEmbedder:
    """Deterministic embedder for pytest. Dense width 8. No download.

    Sparse weights use the BM25 formula in retrieval.sparse, not raw counts.
    """

    id: str = "fake:deterministic"
    dense_size: int = 8
    sparse_id: str = FAKE_SPARSE_ID

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        return [
            TextEmbedding(
                dense=_dense_from_tokens(text, self.dense_size), sparse=bm25_document(text)
            )
            for text in texts
        ]

    async def embed_query(self, text: str) -> TextEmbedding:
        return TextEmbedding(
            dense=_dense_from_tokens(text, self.dense_size), sparse=bm25_query(text)
        )

    def sparse_query_terms(self, text: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(bm25_terms(text)))


class FastEmbedEmbedder:
    """Local dense model. Sparse side is FastEmbed Qdrant/bm25."""

    id: str
    dense_size: int
    sparse_id: str = FASTEMBED_SPARSE_ID

    def __init__(self, spec: EmbedderSpec, *, threads: int) -> None:
        self.id = spec.embedder_id
        self.dense_size = spec.dense_size
        self._spec = spec
        self._threads = threads
        self._model: FastTextEmbedding | None = None
        self._lock = threading.Lock()

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        if not texts:
            return []
        vectors, sparse = await asyncio.gather(
            asyncio.to_thread(self._passage_vectors, texts),
            asyncio.to_thread(_sparse_documents, texts),
        )
        return [
            TextEmbedding(dense=dense, sparse=sparse_vec)
            for dense, sparse_vec in zip(vectors, sparse, strict=True)
        ]

    async def embed_query(self, text: str) -> TextEmbedding:
        prefixed = f"{self._spec.query_prefix}{text}" if self._spec.query_prefix else text
        vectors = await asyncio.to_thread(self._passage_vectors, [prefixed])
        sparse = await asyncio.to_thread(_sparse_query, text)
        return TextEmbedding(dense=vectors[0], sparse=sparse)

    def sparse_query_terms(self, text: str) -> tuple[str, ...]:
        return _fastembed_query_terms(text)

    def _passage_vectors(self, texts: list[str]) -> list[tuple[float, ...]]:
        model = self._load()
        embedded = model.embed(texts, **_fastembed_document_kwargs(len(texts)))
        vectors = [tuple(float(value) for value in vector) for vector in embedded]
        if len(vectors) != len(texts):
            raise RetrievalError("fastembed returned a different number of vectors than texts")
        for vector in vectors:
            if len(vector) != self.dense_size:
                raise RetrievalError(
                    f"fastembed returned {len(vector)} dims, expected {self.dense_size}"
                )
        return vectors

    def _load(self) -> FastTextEmbedding:
        _forbid_fastembed()
        loaded = self._model
        if loaded is not None:
            return loaded
        with self._lock:
            loaded = self._model
            if loaded is None:
                from fastembed import TextEmbedding as FastTextEmbedding

                loaded = FastTextEmbedding(
                    model_name=self._spec.model_name,
                    threads=_thread_count(self._threads),
                )
                self._model = loaded
        return loaded


_LOCAL: dict[EmbedderName, FastEmbedEmbedder] = {}
_LOCAL_LOCK = threading.Lock()
_SPARSE: FastSparseEmbedding | None = None
_SPARSE_LOCK = threading.Lock()
_OPENAI_BATCH = 256


class OpenAIEmbedder:
    """text-embedding-3-small over httpx. Sparse side is FastEmbed Qdrant/bm25."""

    sparse_id: str = FASTEMBED_SPARSE_ID

    def __init__(self, spec: EmbedderSpec, api_key: str) -> None:
        self.id = spec.embedder_id
        self.dense_size = spec.dense_size
        self._model_name = spec.model_name
        self._api_key = api_key

    def __repr__(self) -> str:
        return f"OpenAIEmbedder(id={self.id!r})"

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        if not texts:
            return []
        vectors = await self._embed(texts)
        sparse = await asyncio.to_thread(_sparse_documents, texts)
        return [
            TextEmbedding(dense=dense, sparse=sparse_vec)
            for dense, sparse_vec in zip(vectors, sparse, strict=True)
        ]

    async def embed_query(self, text: str) -> TextEmbedding:
        vectors = await self._embed([text])
        sparse = await asyncio.to_thread(_sparse_query, text)
        return TextEmbedding(dense=vectors[0], sparse=sparse)

    def sparse_query_terms(self, text: str) -> tuple[str, ...]:
        return _fastembed_query_terms(text)

    async def _embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        import httpx

        out: list[tuple[float, ...]] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for start in range(0, len(texts), _OPENAI_BATCH):
                    batch = texts[start : start + _OPENAI_BATCH]
                    response = await client.post(
                        "https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={"model": self._model_name, "input": batch},
                    )
                    if response.status_code != 200:
                        raise RetrievalError(f"openai embeddings HTTP {response.status_code}")
                    out.extend(
                        _parse_openai(response.json(), expected=len(batch), width=self.dense_size)
                    )
        except httpx.HTTPError as exc:
            raise RetrievalError("openai embeddings request failed") from exc
        return out


def build_embedder(
    settings_embedder: EmbedderName,
    api_key: str | None,
    *,
    threads: int = 0,
) -> Embedder:
    spec = spec_for(settings_embedder)
    if spec.kind == "openai":
        key = (api_key or "").strip()
        if not key:
            raise EmbedderNotConfigured("EMBEDDER=openai-small requires OPENAI_API_KEY")
        return OpenAIEmbedder(spec, key)
    with _LOCAL_LOCK:
        cached = _LOCAL.get(settings_embedder)
        if cached is not None:
            return cached
        embedder = FastEmbedEmbedder(spec, threads=threads)
        _LOCAL[settings_embedder] = embedder
        return embedder


def _thread_count(threads: int) -> int:
    if threads > 0:
        return threads
    return os.cpu_count() or 4


def _sparse_threads() -> int:
    raw = os.environ.get("EMBED_THREADS", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def _forbid_fastembed() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("FastEmbed must not run under pytest")


def _load_sparse() -> FastSparseEmbedding:
    _forbid_fastembed()
    global _SPARSE
    loaded = _SPARSE
    if loaded is not None:
        return loaded
    with _SPARSE_LOCK:
        loaded = _SPARSE
        if loaded is None:
            from fastembed.sparse import SparseTextEmbedding

            loaded = SparseTextEmbedding(
                model_name=SPARSE_MODEL,
                threads=_thread_count(_sparse_threads()),
            )
            _SPARSE = loaded
    return loaded


def _fastembed_document_kwargs(_n: int) -> dict[str, int]:
    """Larger ONNX batches. Process-pool parallel=0 OOMs a 4-core runner."""
    return {"batch_size": 256}


def _sparse_documents(texts: list[str]) -> list[SparseTermVector]:
    model = _load_sparse()
    embedded = list(model.embed(texts, **_fastembed_document_kwargs(len(texts))))
    if len(embedded) != len(texts):
        raise RetrievalError("fastembed bm25 returned a different number of vectors than texts")
    return [_from_fastembed_sparse(item) for item in embedded]


def _sparse_query(text: str) -> SparseTermVector:
    model = _load_sparse()
    return _from_fastembed_sparse(next(iter(model.query_embed(text))))


def _fastembed_query_terms(text: str) -> tuple[str, ...]:
    from fastembed.common.utils import remove_non_alphanumeric

    inner: Any = _load_sparse().model
    cleaned = remove_non_alphanumeric(text)
    stemmed = inner._stem(inner.tokenizer.tokenize(cleaned))
    return tuple(dict.fromkeys(stemmed))


def _from_fastembed_sparse(embedding: Any) -> SparseTermVector:
    indices = embedding.indices
    values = embedding.values
    pairs = sorted(
        ((int(index), float(value)) for index, value in zip(indices, values, strict=True)),
        key=lambda item: item[0],
    )
    return SparseTermVector(
        indices=tuple(index for index, _ in pairs),
        values=tuple(value for _, value in pairs),
    )


def _dense_from_tokens(text: str, size: int) -> tuple[float, ...]:
    acc = [0.0] * size
    for token in tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for index in range(size):
            acc[index] += digest[index] / 127.5 - 1.0
    norm = math.sqrt(sum(value * value for value in acc))
    if norm == 0.0:
        # Cosine is undefined for the zero vector. Punctuation-only text still needs a point.
        acc[0] = 1.0
        return tuple(acc)
    return tuple(value / norm for value in acc)


def _parse_openai(payload: object, *, expected: int, width: int) -> list[tuple[float, ...]]:
    if not isinstance(payload, dict):
        raise RetrievalError("openai embeddings response was not an object")
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) != expected:
        raise RetrievalError("openai embeddings response missing data")
    ordered: list[tuple[int, tuple[float, ...]]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RetrievalError("openai embeddings row was not an object")
        index = row.get("index")
        embedding = row.get("embedding")
        if not isinstance(index, int) or not isinstance(embedding, list):
            raise RetrievalError("openai embeddings row missing index or embedding")
        numeric = all(isinstance(value, int | float) for value in embedding)
        if len(embedding) != width or not numeric:
            raise RetrievalError(f"openai embeddings width {len(embedding)} != {width}")
        ordered.append((index, tuple(float(value) for value in embedding)))
    ordered.sort(key=lambda item: item[0])
    if [index for index, _ in ordered] != list(range(expected)):
        raise RetrievalError("openai embeddings indexes were not 0..n-1")
    return [vector for _, vector in ordered]
