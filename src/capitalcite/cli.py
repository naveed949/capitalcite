"""Command-line entry point."""

from __future__ import annotations

import asyncio
import logging
from importlib.metadata import version
from pathlib import Path

import typer

from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.config import get_settings
from capitalcite.ingest.client import (
    EdgarAccessError,
    EdgarClient,
    EdgarError,
    EdgarUnavailableError,
    UnknownTickerError,
)
from capitalcite.ingest.parse import ParseError, parse_forms, parse_tickers, parse_years
from capitalcite.ingest.service import run_ingest
from capitalcite.ingest.store import FilingStore
from capitalcite.logging import configure_logging
from capitalcite.retrieval import (
    EmbedderName,
    IndexReceipt,
    IndexUnavailable,
    RetrievalError,
    build_index,
    create_retrieval_setup,
)

app = typer.Typer(
    name="capitalcite",
    help="Cited, evaluated RAG over U.S. bank filings.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Cited, evaluated RAG over U.S. bank filings."""


@app.command("version")
def show_version() -> None:
    """Print the installed package version."""
    typer.echo(version("capitalcite"))


golden_app = typer.Typer(
    help="Validate the committed golden Q&A set. No network.",
    no_args_is_help=True,
)
app.add_typer(golden_app, name="golden")


@golden_app.command("validate")
def golden_validate(
    path: Path = typer.Option(
        Path("evals/golden"),
        "--path",
        help="Directory with v1.jsonl and passages/.",
    ),
    against_raw: Path | None = typer.Option(
        None,
        "--against-raw",
        help="Ingest root with raw/edgar. Align handwritten quotes to parsed document.md.",
    ),
) -> None:
    """Check schema, mix targets, and that every quote is in a stored passage."""
    from capitalcite.golden.raw import validate_against_raw
    from capitalcite.golden.store import ValidationError, load_golden_set

    try:
        golden = load_golden_set(path)
        stats = golden.validate()
    except ValidationError as exc:
        typer.echo(f"error: golden validation failed\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        "golden "
        f"items={stats.items} "
        f"answerable={stats.answerable} "
        f"unanswerable_or_trick={stats.unanswerable_or_trick} "
        f"unanswerable_or_trick_pct={stats.unanswerable_or_trick_pct:.3f} "
        f"numeric={stats.numeric} "
        f"numeric_pct={stats.numeric_pct:.3f} "
        f"handwritten={stats.handwritten} "
        f"financebench={stats.financebench} "
        f"finqa={stats.finqa}"
    )
    if against_raw is None:
        return
    try:
        report = validate_against_raw(golden.items, against_raw)
    except ValidationError as exc:
        typer.echo(f"error: golden --against-raw failed\n{exc}", err=True)
        raise typer.Exit(code=1) from exc
    if report.errors:
        typer.echo("error: golden --against-raw failed\n" + "\n".join(report.errors), err=True)
        raise typer.Exit(code=1)
    typer.echo(
        "golden-against-raw "
        f"aligned={report.aligned} "
        f"skipped_benchmark={report.skipped_benchmark} "
        "recall=handwritten-only"
    )


@app.command("ingest")
def ingest_cmd(
    tickers: str = typer.Option(
        ...,
        "--tickers",
        help="Comma-separated tickers, e.g. JPM,BAC,WFC,C.",
    ),
    forms: str = typer.Option(
        ...,
        "--forms",
        help="Comma-separated SEC forms, e.g. 10-K or 10-K,10-Q.",
    ),
    years: str = typer.Option(
        ...,
        "--years",
        help="Year, comma list, or inclusive range, e.g. 2023-2025.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Root directory for raw filings (defaults to DATA_DIR, usually data/).",
    ),
) -> None:
    """Download EDGAR primary documents and write sidecar metadata.

    Looks up CIKs via company_tickers.json, lists filings on
    data.sec.gov/submissions, and stores each primary document under
    ``{output}/raw/edgar/{ticker}/{form}/{period}/{accession}/``.
    Reruns skip files that already exist. Requires SEC_USER_AGENT.
    """
    try:
        parsed_tickers = parse_tickers(tickers)
        parsed_forms = parse_forms(forms)
        parsed_years = parse_years(years)
    except ParseError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    settings = get_settings()
    configure_logging(settings.log_level)
    root = output_dir if output_dir is not None else settings.data_dir
    store = FilingStore(root)
    client = create_edgar_client(settings.sec_user_agent)

    async def _run() -> None:
        async with client:
            summary = await run_ingest(
                client=client,
                store=store,
                tickers=parsed_tickers,
                forms=parsed_forms,
                years=parsed_years,
            )
        log = logging.getLogger("capitalcite.ingest")
        for meta in summary.downloaded:
            log.info("downloaded %s %s %s %s", meta.ticker, meta.form, meta.period, meta.url)
        for filing, reason in summary.failed:
            typer.echo(
                (
                    f"error: {filing.ticker} {filing.form} {filing.period} "
                    f"{filing.accession}: {reason}"
                ),
                err=True,
            )
        typer.echo(
            "ingest "
            f"downloaded={summary.downloaded_count} "
            f"skipped={summary.skipped_count} "
            f"failed={summary.failed_count} "
            f"filings={summary.filing_count} "
            f"output={root}"
        )
        if summary.failed_count:
            raise typer.Exit(code=1)
        if summary.filing_count == 0:
            typer.echo("error: no filings matched tickers/forms/years", err=True)
            raise typer.Exit(code=1)

    try:
        asyncio.run(_run())
    except UnknownTickerError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except EdgarAccessError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except EdgarUnavailableError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except EdgarError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("chunk")
def chunk_cmd(
    input_dir: Path | None = typer.Option(
        None,
        "--input-dir",
        help="Root that contains raw/edgar (defaults to DATA_DIR).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Root for parsed/edgar (defaults to --input-dir).",
    ),
    chunker: ChunkerName | None = typer.Option(
        None,
        "--chunker",
        help="fixed, recursive, or section. Defaults to CHUNKER.",
    ),
    size: int | None = typer.Option(
        None,
        "--size",
        help="Target chunk size in tokens. Defaults to CHUNK_SIZE.",
    ),
    overlap: int | None = typer.Option(
        None,
        "--overlap",
        help="Overlap in tokens. Defaults to CHUNK_OVERLAP.",
    ),
) -> None:
    """Parse stored filings and write chunks with citation metadata.

    Reads ``{input}/raw/edgar/.../{primaryDocument}`` plus ``metadata.json``.
    Writes ``{output}/parsed/edgar/.../document.md``, ``chunks.jsonl``, and
    ``stats.json``. The chunker is selected by ``--chunker`` or ``CHUNKER``.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    source_root = input_dir if input_dir is not None else settings.data_dir
    dest_root = output_dir if output_dir is not None else source_root
    chosen = chunker if chunker is not None else settings.chunker
    chosen_size = size if size is not None else settings.chunk_size
    chosen_overlap = overlap if overlap is not None else settings.chunk_overlap
    if chosen_size < 1:
        typer.echo("error: --size must be at least 1", err=True)
        raise typer.Exit(code=1)
    if chosen_overlap < 0 or chosen_overlap >= chosen_size:
        typer.echo("error: --overlap must be >= 0 and smaller than --size", err=True)
        raise typer.Exit(code=1)

    summary = run_chunk(
        input_root=source_root,
        output=FilingStore(dest_root),
        chunker=chosen,
        size=chosen_size,
        overlap=chosen_overlap,
    )
    for path, reason in summary.failed:
        typer.echo(f"error: {path}: {reason}", err=True)
    typer.echo(
        "chunk "
        f"strategy={chosen} "
        f"files={summary.files} "
        f"chunks={summary.chunks} "
        f"mean_tokens={summary.mean_tokens:.1f} "
        f"failed={len(summary.failed)} "
        f"output={dest_root}"
    )
    if summary.failed:
        raise typer.Exit(code=1)
    if summary.files == 0:
        typer.echo("error: no filings found under raw/edgar", err=True)
        raise typer.Exit(code=1)


@app.command("index")
def index_cmd(
    input_dir: Path | None = typer.Option(
        None,
        "--input-dir",
        help="Root that contains parsed/edgar (defaults to DATA_DIR).",
    ),
    chunker: ChunkerName | None = typer.Option(
        None,
        "--chunker",
        help="fixed, recursive, or section. Defaults to CHUNKER. Does not re-chunk.",
    ),
    size: int | None = typer.Option(
        None,
        "--size",
        help="Chunk size recorded in the index config. Defaults to CHUNK_SIZE.",
    ),
    overlap: int | None = typer.Option(
        None,
        "--overlap",
        help="Chunk overlap recorded in the index config. Defaults to CHUNK_OVERLAP.",
    ),
    embedder: EmbedderName | None = typer.Option(
        None,
        "--embedder",
        help="bge-small, minilm, or openai-small. Defaults to EMBEDDER.",
    ),
) -> None:
    """Embed chunks.jsonl into Qdrant and write an index sidecar.

    Reads ``{input}/parsed/edgar/**/chunks.jsonl``. Does not parse or chunk.
    Upserts points, deletes orphans, then writes
    ``{input}/index/{collection}.json``.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    root = input_dir if input_dir is not None else settings.data_dir
    chosen_chunker = chunker if chunker is not None else settings.chunker
    chosen_size = size if size is not None else settings.chunk_size
    chosen_overlap = overlap if overlap is not None else settings.chunk_overlap
    chosen_embedder = embedder.value if embedder is not None else settings.embedder
    if chosen_size < 1:
        typer.echo("error: --size must be at least 1", err=True)
        raise typer.Exit(code=1)
    if chosen_overlap < 0 or chosen_overlap >= chosen_size:
        typer.echo("error: --overlap must be >= 0 and smaller than --size", err=True)
        raise typer.Exit(code=1)
    chosen = settings.model_copy(
        update={
            "chunker": chosen_chunker,
            "chunk_size": chosen_size,
            "chunk_overlap": chosen_overlap,
            "embedder": chosen_embedder,
        }
    )
    try:

        async def _run() -> IndexReceipt:
            setup = create_retrieval_setup(chosen)
            try:
                return await build_index(setup, root)
            finally:
                await setup.aclose()

        receipt = asyncio.run(_run())
    except IndexUnavailable as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except RetrievalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        "index "
        f"collection={receipt.collection_name} "
        f"config_hash={receipt.config_hash} "
        f"points={receipt.point_count} "
        f"output={root}"
    )


def create_edgar_client(user_agent: str) -> EdgarClient:
    """Build the live EDGAR client. Tests patch this to inject a transport."""
    return EdgarClient(user_agent=user_agent)
