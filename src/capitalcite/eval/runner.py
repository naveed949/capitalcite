"""Chunk, index, retrieve, and score one golden suite."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from capitalcite.atomic import atomic_write_text
from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.config import Settings
from capitalcite.eval.agent_compare import compare_agent_vs_rag, hop_items
from capitalcite.eval.cache_key import EVAL_COLLECTION_PREFIX
from capitalcite.eval.diagnose import diagnose_bm25, items_for_probe
from capitalcite.eval.judge import recall_items, score_item
from capitalcite.eval.models import AgentVsRag, AggregateScore, ArmResult, EvalReport
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
from capitalcite.trace import ObservationKind, eval_attrs, observation


class EvalFailed(Exception):
    """The suite cannot run. CLI exits 1."""


class EvalUnverified(Exception):
    """A required store or model is missing. CLI exits 2."""


_FIXTURE_CORPUS = Path("tests/fixtures/chunking/corpus")
_DEFAULT_CHUNKERS = (ChunkerName.SECTION, ChunkerName.FIXED)
_DEFAULT_MODES = (RetrievalMode.DENSE, RetrievalMode.BM25, RetrievalMode.HYBRID)
_RETRIEVE_K = 10
logger = logging.getLogger(__name__)


def _timing(message: str) -> None:
    """Phase timings must show in GitHub Actions stdout, not only the log handler."""
    logger.info(message)
    print(message, flush=True)


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
    hops = hop_items(items)
    agent_vs_rag: AgentVsRag | None = None
    git_sha = _git_sha()

    with observation(
        "eval",
        ObservationKind.EVAL,
        **eval_attrs(git_sha=git_sha, suite=suite, small=small),
    ) as span:
        with tempfile.TemporaryDirectory(prefix="capitalcite-eval-") as tmp:
            work_root = Path(tmp)
            if small or settings.qdrant_path is None:
                qdrant_dir = work_root / "qdrant"
            else:
                qdrant_dir = settings.qdrant_path
            qdrant_dir.mkdir(parents=True, exist_ok=True)
            for chunker in chunkers:
                work = work_root / chunker.value
                chunk_started = time.perf_counter()
                summary = run_chunk(
                    input_root=source,
                    output=FilingStore(work),
                    chunker=chunker,
                    size=settings.chunk_size,
                    overlap=settings.chunk_overlap,
                )
                chunk_s = time.perf_counter() - chunk_started
                if summary.failed:
                    reasons = "; ".join(f"{path}: {reason}" for path, reason in summary.failed)
                    raise EvalFailed(f"chunk failed: {reasons}")
                if summary.files == 0:
                    raise EvalUnverified(f"no filings to chunk under {source}")
                _timing(
                    "eval timing phase=chunk "
                    f"chunker={chunker.value} files={summary.files} "
                    f"chunks={summary.chunks} chunk_s={chunk_s:.3f}"
                )

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
                    index_started = time.perf_counter()
                    receipt = await build_index(setup, work)
                    index_s = time.perf_counter() - index_started
                    _timing(
                        "eval timing phase=index "
                        f"chunker={chunker.value} chunks={summary.chunks} "
                        f"points={receipt.point_count} index_s={index_s:.3f}"
                    )
                    probe_s = 0.0
                    if probe_items:
                        probe_started = time.perf_counter()
                        diagnostics.append(await diagnose_bm25(setup, probe_items))
                        probe_s = time.perf_counter() - probe_started
                        _timing(
                            "eval timing phase=probe "
                            f"chunker={chunker.value} items={len(probe_items)} "
                            f"probe_s={probe_s:.3f}"
                        )
                    query_s = 0.0
                    for mode in modes:
                        with observation(
                            "eval-arm",
                            ObservationKind.EVAL,
                            **eval_attrs(
                                git_sha=git_sha,
                                suite=suite,
                                small=small,
                                config_hash=receipt.config_hash,
                            ),
                        ) as arm:
                            arm.set(
                                **{
                                    "capitalcite.mode": mode.value,
                                    "capitalcite.collection": receipt.collection_name,
                                }
                            )
                            query_started = time.perf_counter()
                            scores = []
                            for item in items:
                                hits = await retrieve(setup, item.question, mode=mode, k=retrieve_k)
                                scores.append(score_item(item, hits))
                            mode_s = time.perf_counter() - query_started
                            query_s += mode_s
                            _timing(
                                "eval timing phase=query "
                                f"chunker={chunker.value} mode={mode.value} "
                                f"items={len(items)} query_s={mode_s:.3f}"
                            )
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
                    agent_s = 0.0
                    if hops and chunker == ChunkerName.SECTION and RetrievalMode.HYBRID in modes:
                        agent_started = time.perf_counter()
                        agent_vs_rag = await compare_agent_vs_rag(
                            setup,
                            hops,
                            chunker=chunker,
                            mode=RetrievalMode.HYBRID,
                            retrieve_k=retrieve_k,
                        )
                        agent_s = time.perf_counter() - agent_started
                        _timing(
                            "eval timing phase=agent_vs_rag "
                            f"chunker={chunker.value} items={len(hops)} "
                            f"agent_s={agent_s:.3f}"
                        )
                    _timing(
                        f"eval timing chunker={chunker.value} files={summary.files} "
                        f"chunks={summary.chunks} chunk_s={chunk_s:.3f} "
                        f"index_s={index_s:.3f} probe_s={probe_s:.3f} "
                        f"query_s={query_s:.3f} agent_s={agent_s:.3f}"
                    )
                except RetrievalError as exc:
                    raise EvalUnverified(str(exc)) from exc
                finally:
                    await setup.aclose()
        report = EvalReport(
            git_sha=git_sha,
            suite=suite,
            small=small,
            k=k,
            n_items=len(items),
            embedder_id=embedder_id,
            sparse_id=sparse_id,
            fastembed_version=_package_version("fastembed"),
            qdrant_client_version=_package_version("qdrant-client"),
            arms=tuple(arms),
            agent_vs_rag=agent_vs_rag,
        )
        span.set(**tag_eval(report))
    if write:
        from capitalcite.eval.report import write_report

        json_path, _ = write_report(report, out_dir)
        if diagnostics:
            atomic_write_text(
                json_path.with_name(f"{json_path.stem}-bm25-diagnose.md"),
                "\n".join(diagnostics),
            )
    return report


def tag_eval(report: EvalReport) -> dict[str, str | int | float | bool]:
    """Root attributes. A single shared config hash is included."""
    hashes = {arm.config_hash for arm in report.arms}
    config_hash = next(iter(hashes)) if len(hashes) == 1 else None
    return eval_attrs(
        git_sha=report.git_sha,
        suite=report.suite,
        small=report.small,
        config_hash=config_hash,
    )


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
            "qdrant_collection": EVAL_COLLECTION_PREFIX,
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
