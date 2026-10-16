"""Dense embedders. Sparse counts always come from term_counts."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import threading
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from capitalcite.retrieval.hashing import term_counts, tokens
from capitalcite.retrieval.types import (
    Embedder,
    EmbedderName,
    EmbedderNotConfigured,
    RetrievalError,
    TextEmbedding,
)

if TYPE_CHECKING:
    from fastembed import TextEmbedding as FastTextEmbedding


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

    Sparse indices come from term_counts, the same encoder as production.
    Dense axes are a hash of the same tokens, so shared words raise cosine.
    """

    id: str = "fake:deterministic"
    dense_size: int = 8

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        return [_embed_fake(text, self.dense_size) for text in texts]

    async def embed_query(self, text: str) -> TextEmbedding:
        return _embed_fake(text, self.dense_size)


class FastEmbedEmbedder:
    """Local dense model. The ONNX file loads on the first embed call."""

    id: str
    dense_size: int

    def __init__(self, spec: EmbedderSpec) -> None:
        self.id = spec.embedder_id
        self.dense_size = spec.dense_size
        self._spec = spec
        self._model: FastTextEmbedding | None = None
        self._lock = threading.Lock()

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        if not texts:
            return []
        vectors = await asyncio.to_thread(self._passage_vectors, texts)
        return [_pair(text, vector) for text, vector in zip(texts, vectors, strict=True)]

    async def embed_query(self, text: str) -> TextEmbedding:
        prefixed = f"{self._spec.query_prefix}{text}" if self._spec.query_prefix else text
        vectors = await asyncio.to_thread(self._passage_vectors, [prefixed])
        return _pair(text, vectors[0])

    def _passage_vectors(self, texts: list[str]) -> list[tuple[float, ...]]:
        model = self._load()
        embedded = model.embed(texts)
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
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise RuntimeError("FastEmbed must not run under pytest")
        loaded = self._model
        if loaded is not None:
            return loaded
        with self._lock:
            loaded = self._model
            if loaded is None:
                from fastembed import TextEmbedding as FastTextEmbedding

                loaded = FastTextEmbedding(model_name=self._spec.model_name)
                self._model = loaded
        return loaded


_LOCAL: dict[EmbedderName, FastEmbedEmbedder] = {}
_LOCAL_LOCK = threading.Lock()


class OpenAIEmbedder:
    """text-embedding-3-small over httpx. Sparse counts stay local."""

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
        return [_pair(text, vector) for text, vector in zip(texts, vectors, strict=True)]

    async def embed_query(self, text: str) -> TextEmbedding:
        vectors = await self._embed([text])
        return _pair(text, vectors[0])

    async def _embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self._model_name, "input": texts},
                )
        except httpx.HTTPError as exc:
            raise RetrievalError("openai embeddings request failed") from exc
        if response.status_code != 200:
            raise RetrievalError(f"openai embeddings HTTP {response.status_code}")
        return _parse_openai(response.json(), expected=len(texts), width=self.dense_size)


def build_embedder(settings_embedder: EmbedderName, api_key: str | None) -> Embedder:
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
        embedder = FastEmbedEmbedder(spec)
        _LOCAL[settings_embedder] = embedder
        return embedder


def _embed_fake(text: str, size: int) -> TextEmbedding:
    return _pair(text, _dense_from_tokens(text, size))


def _pair(text: str, dense: tuple[float, ...]) -> TextEmbedding:
    return TextEmbedding(dense=dense, sparse=term_counts(text))


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
