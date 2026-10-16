"""Index identity, filters, and the hit a caller sees."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from capitalcite.chunking.models import Chunk, ChunkerName

_EXACT_PERIOD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_PERIOD = re.compile(r"^\d{4}$")


class EmbedderName(StrEnum):
    BGE_SMALL = "bge-small"
    MINILM = "minilm"
    OPENAI_SMALL = "openai-small"


class RetrievalMode(StrEnum):
    DENSE = "dense"
    BM25 = "bm25"
    HYBRID = "hybrid"


class RetrievalError(Exception):
    """Domain failure. Routes map these. Retrieval does not raise HTTPException."""


class IndexUnavailable(RetrievalError):
    """The Qdrant RPC failed. The collection may or may not exist."""


class IndexNotBuilt(RetrievalError):
    """No collection exists for this IndexConfig."""

    def __init__(self, collection_name: str) -> None:
        self.collection_name = collection_name
        super().__init__(f"index collection {collection_name} is not built")


class EmbedderNotConfigured(RetrievalError):
    """EMBEDDER=openai-small and OPENAI_API_KEY is empty."""


class CorruptChunks(RetrievalError):
    """A chunks.jsonl line is not a Chunk for this config. The sidecar was not written."""


class SparseTermVector(BaseModel):
    """Token indices and raw term counts. Qdrant Modifier.IDF supplies IDF."""

    model_config = ConfigDict(frozen=True)
    indices: tuple[int, ...]
    values: tuple[float, ...]


class TextEmbedding(BaseModel):
    model_config = ConfigDict(frozen=True)
    dense: tuple[float, ...]
    sparse: SparseTermVector


class Embedder(Protocol):
    """Text in, dense vector and sparse counts out. No Qdrant types."""

    id: str
    dense_size: int

    async def embed_documents(self, texts: list[str]) -> list[TextEmbedding]:
        """Embed Chunk.text. No query prefix on the dense vector."""
        ...

    async def embed_query(self, text: str) -> TextEmbedding:
        """Dense vector may use a query prefix. Sparse vector must not."""
        ...


class IndexConfig(BaseModel):
    """Canonical index identity. Hash this record. Do not hash the Qdrant URL or k.

    collection_name is ``{collection_prefix}-{config_hash}``.
    """

    model_config = ConfigDict(frozen=True)
    embedder_id: str = Field(min_length=1)
    dense_size: int = Field(ge=1)
    chunker: ChunkerName
    chunk_size: int = Field(ge=1)
    chunk_overlap: int = Field(ge=0)
    collection_prefix: str = Field(min_length=1)
    distance: Literal["cosine"] = "cosine"
    dense_vector: Literal["dense"] = "dense"
    sparse_vector: Literal["bm25"] = "bm25"
    sparse_modifier: Literal["idf"] = "idf"


class ExactPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["exact"] = "exact"
    period: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class YearPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["year"] = "year"
    year: int = Field(ge=1900, le=2100)


class SearchFilters(BaseModel):
    """Trusted filter. Build one only through parse_filters."""

    model_config = ConfigDict(frozen=True)
    ticker: str | None = None
    form: str | None = None
    period: ExactPeriod | YearPeriod | None = None


class Hit(BaseModel):
    """One ranked citation. score is the Qdrant score for the mode that ran."""

    model_config = ConfigDict(frozen=True)
    score: float
    chunk: Chunk

    @computed_field  # type: ignore[prop-decorator]
    @property
    def year(self) -> int | None:
        # hashing imports SparseTermVector from this module.
        from capitalcite.retrieval.hashing import year_of

        return year_of(self.chunk.period)


class IndexReceipt(BaseModel):
    """Sidecar body at ``{input_dir}/index/{collection_name}.json``."""

    model_config = ConfigDict(frozen=True)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_name: str
    embedder_id: str
    point_count: int = Field(ge=0)
    canonical: IndexConfig


def parse_filters(
    *,
    ticker: str | None = None,
    form: str | None = None,
    period: str | None = None,
) -> SearchFilters:
    """Boundary parser.

    Ticker and form are uppercased. Blank strings become None.
    period YYYY becomes YearPeriod. YYYY-MM-DD becomes ExactPeriod.
    Any other period raises ValueError.
    """
    ticker_text = _blank(ticker)
    form_text = _blank(form)
    period_text = _blank(period)
    parsed: ExactPeriod | YearPeriod | None = None
    if period_text is not None:
        if _YEAR_PERIOD.fullmatch(period_text):
            try:
                parsed = YearPeriod(year=int(period_text))
            except ValidationError as exc:
                raise ValueError("period year is out of range") from exc
        elif _EXACT_PERIOD.fullmatch(period_text):
            parsed = ExactPeriod(period=period_text)
        else:
            raise ValueError("period must be YYYY-MM-DD or a 4-digit year")
    return SearchFilters(
        ticker=ticker_text.upper() if ticker_text is not None else None,
        form=form_text.upper() if form_text is not None else None,
        period=parsed,
    )


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
