"""Section-chunk the fixture corpus into an in-memory index.

check and the metrics suite share this setup. The retrieval pack stays
FakeEmbedder, section chunks, and :memory: Qdrant.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.config import Settings
from capitalcite.eval.runner import EvalUnverified
from capitalcite.ingest.store import FilingStore
from capitalcite.retrieval import (
    EmbedderName,
    FakeEmbedder,
    RetrievalError,
    RetrievalSetup,
    build_index,
    setup_retrieval,
)

FIXTURE_RETRIEVE_K = 10
_CORPUS = Path("tests/fixtures/chunking/corpus")


class FixtureIndexError(Exception):
    """The fixture corpus did not chunk. Callers map this to their own failure."""


def fixture_settings() -> Settings:
    return Settings(
        sec_user_agent="capitalcite-eval eval@example.com",
        chunker=ChunkerName.SECTION,
        chunk_size=512,
        chunk_overlap=64,
        embedder="bge-small",
        qdrant_url=":memory:",
    )


@asynccontextmanager
async def indexed_fixture(root: Path | None = None) -> AsyncIterator[RetrievalSetup]:
    corpus = root if root is not None else _CORPUS
    if not (corpus / "raw" / "edgar").is_dir():
        raise EvalUnverified(f"missing raw/edgar under {corpus}")
    with tempfile.TemporaryDirectory(prefix="capitalcite-check-") as tmp:
        work = Path(tmp)
        summary = run_chunk(
            input_root=corpus,
            output=FilingStore(work),
            chunker=ChunkerName.SECTION,
            size=512,
            overlap=64,
        )
        if summary.failed:
            raise FixtureIndexError("fixture chunk failed")
        if summary.files == 0:
            raise EvalUnverified(f"no filings to chunk under {corpus}")
        settings = fixture_settings()
        client = AsyncQdrantClient(location=":memory:", timeout=5, check_compatibility=False)
        setup = setup_retrieval(
            settings.model_copy(
                update={
                    "chunker": ChunkerName.SECTION,
                    "embedder": EmbedderName.BGE_SMALL.value,
                    "qdrant_collection": "check",
                    "search_k": FIXTURE_RETRIEVE_K,
                    "qdrant_url": ":memory:",
                    "qdrant_path": None,
                }
            ),
            client=client,
            embedder=FakeEmbedder(),
        )
        try:
            await build_index(setup, work)
            yield setup
        except RetrievalError as exc:
            raise EvalUnverified(str(exc)) from exc
        finally:
            await setup.aclose()
