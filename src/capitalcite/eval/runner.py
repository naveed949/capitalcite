"""Chunk, index, retrieve, and score one golden suite."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from capitalcite.atomic import atomic_write_text
from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.config import Settings
from capitalcite.eval.diagnose import diagnose_bm25, items_for_probe
from capitalcite.eval.judge import recall_items, score_item
from capitalcite.eval.models import AggregateScore, ArmResult, EvalReport
from capitalcite.golden.store import ValidationError, load_golden_set
from capitalcite.ingest.store import FilingStore
from capitalcite.retrieval import (
    EmbedderName,
    FakeEmbedder,
    RetrievalError,
    RetrievalMode,
    RetrievalSetup,
    build_index,
    create_retrieval_setup,
    retrieve,
    setup_retrieval,
)


class EvalFailed(Exception):
    """The suite cannot run. CLI exits 1."""


class EvalUnverified(Exception):
    """A required store or model is missing. CLI exits 2."""


_FIXTURE_CORPUS = Path("tests/fixtures/chunking/corpus")
_DEFAULT_CHUNKERS = (ChunkerName.SECTION, ChunkerName.FIXED)
_DEFAULT_MODES = (RetrievalMode.DENSE, RetrievalMode.BM25, RetrievalMode.HYBRID)
_RETRIEVE_K = 10


async def run_suite(
    *,
    suite: str,
    small: bool,
    input_dir: Path | None,
    out_dir: Path,
    golden_dir: Path,
    chunkers: Sequence[ChunkerName],
    modes: Sequence[RetrievalMode],
    embedder: EmbedderName | None,
    k: int,
    settings: Settings,
    write: bool = True,
) -> EvalReport:
    if suite != "golden":
        raise EvalFailed(f"unknown suite {suite!r}")
    if k < 1:
        raise EvalFailed("k must be at least 1")
    if small and embedder is not None:
        raise EvalFailed("--small does not take --embedder")
    if small and input_dir is not None:
        raise EvalFailed("--small does not take --input-dir")
    if not chunkers:
        raise EvalFailed("need at least one chunker")
    if not modes:
        raise EvalFailed("need at least one mode")

    try:
        golden = load_golden_set(golden_dir)
        golden.validate()
    except (ValidationError, OSError) as exc:
        raise EvalFailed(str(exc)) from exc
    items = recall_items(golden.items)
    if not items:
        raise EvalFailed("no handwritten evidence spans")

    if small:
        source = _FIXTURE_CORPUS
    elif input_dir is not None:
        source = input_dir
    else:
        source = settings.data_dir
    if not (source / "raw" / "edgar").is_dir():
        raise EvalUnverified(f"missing raw/edgar under {source}")

    chosen_embedder = EmbedderName.BGE_SMALL if embedder is None else embedder
    retrieve_k = max(k, _RETRIEVE_K)
    arms: list[ArmResult] = []
    embedder_id = "fake:deterministic" if small else ""
    sparse_id = ""
    diagnostics: list[str] = []
    probe_items = items_for_probe(items)

    with tempfile.TemporaryDirectory(prefix="capitalcite-eval-") as tmp:
        work_root = Path(tmp)
        qdrant_dir = work_root / "qdrant"
        qdrant_dir.mkdir()
        for chunker in chunkers:
            work = work_root / chunker.value
            summary = run_chunk(
                input_root=source,
                output=FilingStore(work),
                chunker=chunker,
                size=settings.chunk_size,
                overlap=settings.chunk_overlap,
            )
            if summary.failed:
                reasons = "; ".join(f"{path}: {reason}" for path, reason in summary.failed)
                raise EvalFailed(f"chunk failed: {reasons}")
            if summary.files == 0:
                raise EvalUnverified(f"no filings to chunk under {source}")

            setup = _setup(
                settings,
                chunker=chunker,
                embedder=chosen_embedder,
                small=small,
                qdrant_dir=qdrant_dir,
                search_k=retrieve_k,
            )
            embedder_id = setup.config.embedder_id
            sparse_id = setup.config.sparse_id
            try:
                receipt = await build_index(setup, work)
                if probe_items:
                    diagnostics.append(await diagnose_bm25(setup, probe_items))
                for mode in modes:
                    scores = []
                    for item in items:
                        hits = await retrieve(setup, item.question, mode=mode, k=retrieve_k)
                        scores.append(score_item(item, hits))
                    arms.append(
                        ArmResult(
                            config_hash=receipt.config_hash,
                            collection_name=receipt.collection_name,
                            embedder_id=receipt.embedder_id,
                            chunker=chunker,
                            chunk_size=setup.config.chunk_size,
                            chunk_overlap=setup.config.chunk_overlap,
                            mode=mode,
                            k=k,
                            rrf_k=setup.rrf_k,
                            point_count=receipt.point_count,
                            metrics=AggregateScore.from_items(scores),
                            item_scores=tuple(scores),
                        )
                    )
            except RetrievalError as exc:
                raise EvalUnverified(str(exc)) from exc
            finally:
                await setup.aclose()

    report = EvalReport(
        git_sha=_git_sha(),
        suite=suite,
        small=small,
        k=k,
        n_items=len(items),
        embedder_id=embedder_id,
        sparse_id=sparse_id,
        fastembed_version=_package_version("fastembed"),
        qdrant_client_version=_package_version("qdrant-client"),
        arms=tuple(arms),
    )
    if write:
        from capitalcite.eval.report import write_report

        json_path, _ = write_report(report, out_dir)
        if diagnostics:
            atomic_write_text(
                json_path.with_name(f"{json_path.stem}-bm25-diagnose.md"),
                "\n".join(diagnostics),
            )
    return report


def _setup(
    settings: Settings,
    *,
    chunker: ChunkerName,
    embedder: EmbedderName,
    small: bool,
    qdrant_dir: Path,
    search_k: int,
) -> RetrievalSetup:
    chosen = settings.model_copy(
        update={
            "chunker": chunker,
            "embedder": embedder.value,
            "qdrant_collection": "filings",
            "search_k": search_k,
            "qdrant_path": None if small else qdrant_dir,
            "qdrant_url": ":memory:" if small else settings.qdrant_url,
        }
    )
    if small:
        client = AsyncQdrantClient(location=":memory:", timeout=5, check_compatibility=False)
        return setup_retrieval(chosen, client=client, embedder=FakeEmbedder())
    return create_retrieval_setup(chosen)


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not sha:
        return "unknown"
    return sha


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return ""
