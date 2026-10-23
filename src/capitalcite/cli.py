"""Command-line entry point."""

from __future__ import annotations

import asyncio
import json
import logging
from importlib.metadata import version
from pathlib import Path

import typer

from capitalcite.chunking.models import ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.config import Settings, get_settings
from capitalcite.eval.baseline import (
    DEFAULT_NIGHTLY,
    Baseline,
    Thresholds,
    TrackedRate,
    compare,
    compare_nightly,
    load_nightly,
    snapshot_nightly,
    write_nightly,
)
from capitalcite.eval.cache_key import eval_index_cache_key
from capitalcite.eval.check import (
    DEFAULT_BASELINE,
    CheckFailed,
    CheckReport,
    load_baseline,
    run_check,
    write_baseline,
)
from capitalcite.eval.graph_eval import render_graph_markdown, run_graph_suite, update_readme_graph
from capitalcite.eval.models import EvalReport
from capitalcite.eval.report import load_reports, render_markdown, update_readme
from capitalcite.eval.runner import EvalFailed, EvalUnverified, run_suite
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
    RetrievalMode,
    build_index,
    create_retrieval_setup,
)
from capitalcite.trace import configure_settings, shutdown

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


@app.command("mcp")
def mcp_serve() -> None:
    """Speak MCP on stdio. stdout is the protocol wire. Logging goes to stderr."""
    from capitalcite.mcp import run_stdio

    run_stdio()


golden_app = typer.Typer(
    help="Validate the committed golden Q&A set. No network.",
    no_args_is_help=True,
)
app.add_typer(golden_app, name="golden")

audit_app = typer.Typer(
    help="Verify the append-only hash-chained audit log. No network.",
    no_args_is_help=True,
)
app.add_typer(audit_app, name="audit")

graph_app = typer.Typer(
    help="Optional entity graph. Memory backend needs no Neo4j.",
    no_args_is_help=True,
)
app.add_typer(graph_app, name="graph")

metrics_app = typer.Typer(
    help="Save companyfacts and lead-bank call reports.",
    no_args_is_help=True,
)
app.add_typer(metrics_app, name="metrics")

FIXTURES = Path("tests/fixtures/metrics")


@audit_app.command("verify")
def audit_verify(
    path: Path = typer.Option(
        Path("data/audit.jsonl"),
        "--path",
        help="JSONL hash chain written by gated tool calls.",
    ),
) -> None:
    """Detect a tampered, inserted, or removed audit record. Offline."""
    from capitalcite.audit import verify

    report = verify(path)
    if report.ok:
        typer.echo(f"audit ok records={report.records}")
        return
    for issue in report.issues:
        seq = f"seq={issue.seq} " if issue.seq is not None else ""
        typer.echo(f"audit fail {seq}reason={issue.reason}", err=True)
    raise typer.Exit(code=1)


gate_app = typer.Typer(
    help="Fail-closed tool gate. No network.",
    no_args_is_help=True,
)
app.add_typer(gate_app, name="gate")


@gate_app.command("validate")
def gate_validate(
    policy: Path | None = typer.Option(
        None,
        "--policy",
        help="Policy JSON. Default is the shipped allow-list.",
    ),
) -> None:
    """Load the policy. Exit 1 if it is broken."""
    from capitalcite.gate import BrokenPolicy, load_policy

    loaded = load_policy(policy)
    if isinstance(loaded, BrokenPolicy):
        typer.echo(f"deny policy_load_failed {loaded.detail}", err=True)
        raise typer.Exit(code=1)
    names = ",".join(sorted(loaded.tools))
    typer.echo(f"ok tools={names}")


@gate_app.command("decide")
def gate_decide(
    tool: str = typer.Option(..., "--tool", help="Tool name the planner requested."),
    args: str = typer.Option("{}", "--args", help="JSON object of tool arguments."),
    policy: Path | None = typer.Option(
        None,
        "--policy",
        help="Policy JSON. Default is the shipped allow-list.",
    ),
) -> None:
    """Print one allow or deny decision. Does not run the tool."""
    from capitalcite.agent.tools import ToolRegistry
    from capitalcite.gate import Gate, load_policy

    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as exc:
        typer.echo("error: --args must be a JSON object", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(parsed, dict):
        typer.echo("error: --args must be a JSON object", err=True)
        raise typer.Exit(code=1)
    gate = Gate(load_policy(policy), ToolRegistry({}))
    configure_settings(get_settings())
    try:
        typer.echo(gate.decide(tool, parsed).model_dump_json())
    finally:
        shutdown()


@gate_app.command("redteam")
def gate_redteam() -> None:
    """Run the prompt-injection suite. Exit 1 if any case fails."""
    from capitalcite.gate import run_redteam

    report = asyncio.run(run_redteam())
    typer.echo(report.model_dump_json())
    if report.failed:
        raise typer.Exit(code=1)


@gate_app.command("bench")
def gate_bench(
    n: int = typer.Option(2000, "--n", help="Number of decide() samples."),
) -> None:
    """Measure decide() p50 and p95 in milliseconds."""
    from capitalcite.gate import bench_decide

    if n < 2:
        typer.echo("error: --n must be at least 2", err=True)
        raise typer.Exit(code=1)
    typer.echo(bench_decide(n).model_dump_json())


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


@graph_app.command("load")
def graph_load(
    backend: str = typer.Option("memory", "--backend", help="memory or neo4j."),
    input_path: Path = typer.Option(
        Path("evals/golden/graph_snapshot.json"),
        "--input",
        help="GraphSnapshot JSON.",
    ),
) -> None:
    """MERGE the snapshot. Running twice keeps the same counts."""
    from capitalcite.kg import GraphStore, MemoryGraphStore, load_snapshot, open_store

    if backend not in {"memory", "neo4j"}:
        typer.echo("error: --backend must be memory or neo4j", err=True)
        raise typer.Exit(code=1)

    async def _run() -> str:
        snapshot = load_snapshot(input_path)
        store: GraphStore
        if backend == "memory":
            store = MemoryGraphStore()
        else:
            settings = get_settings()
            opened = open_store(settings)
            if opened is None:
                raise EvalUnverified("NEO4J_URI is empty")
            store = opened
        try:
            receipt = await store.load(snapshot)
        finally:
            await store.close()
        return (
            "graph-load "
            f"backend={receipt.backend} "
            f"banks={receipt.banks} "
            f"filings={receipt.filings} "
            f"metrics={receipt.metrics} "
            f"edges={receipt.edges}"
        )

    try:
        typer.echo(asyncio.run(_run()))
    except EvalUnverified as exc:
        typer.echo(f"unverified: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@graph_app.command("query")
def graph_query_cmd(
    kind: str = typer.Option(..., "--kind", help="subsidiaries, parent, filings, metric, or path."),
    ticker: str | None = typer.Option(None, "--ticker"),
    rssd: str | None = typer.Option(None, "--rssd"),
    name: str | None = typer.Option(None, "--name", help="Metric name."),
    period: str | None = typer.Option(None, "--period"),
    form: str | None = typer.Option(None, "--form"),
    depth: int | None = typer.Option(None, "--depth", min=1, max=4, help="1 to 4 hops."),
    backend: str = typer.Option("memory", "--backend", help="memory or neo4j."),
    input_path: Path = typer.Option(
        Path("evals/golden/graph_snapshot.json"),
        "--input",
        help="Snapshot loaded into memory before the query.",
    ),
) -> None:
    """Run one typed graph_query. Memory loads the snapshot in process."""
    from capitalcite.kg import (
        GraphQuery,
        GraphQueryKind,
        GraphStore,
        MemoryGraphStore,
        load_snapshot,
        open_store,
    )

    async def _run() -> str:
        query = GraphQuery(
            kind=GraphQueryKind(kind),
            ticker=ticker,
            rssd=rssd,
            name=name,
            period=period,
            form=form,
            depth=depth if depth is not None else 1,
        )
        store: GraphStore
        if backend == "memory":
            store = MemoryGraphStore()
            await store.load(load_snapshot(input_path))
        elif backend == "neo4j":
            settings = get_settings()
            opened = open_store(settings)
            if opened is None:
                raise EvalUnverified("NEO4J_URI is empty")
            store = opened
        else:
            raise ValueError("--backend must be memory or neo4j")
        try:
            result = await store.query(query)
        finally:
            await store.close()
        status = "ok" if result.ok else "fail"
        return f"graph-query {status} {result.detail}"

    try:
        typer.echo(asyncio.run(_run()))
    except EvalUnverified as exc:
        typer.echo(f"unverified: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@metrics_app.command("extract")
def metrics_extract(
    tickers: str = typer.Option(
        ...,
        "--tickers",
        help="Comma-separated tickers, e.g. JPM,BAC,WFC,C.",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help="Root for raw snapshots. Defaults to DATA_DIR.",
    ),
    fixtures: bool = typer.Option(
        False,
        "--fixtures",
        help="Copy tests/fixtures/metrics. No network.",
    ),
) -> None:
    """Write SEC companyfacts and FDIC lead-bank financials as separate files."""
    from capitalcite.agent.http import HttpxJsonClient
    from capitalcite.holdings import holding_company
    from capitalcite.ingest.rate_limit import shared_limiter
    from capitalcite.metrics import (
        FixtureBatch,
        LiveBatch,
        MetricsInputError,
        MetricsStore,
        MetricsUnavailable,
        run_extract,
    )
    from capitalcite.metrics.extract import utc_now

    try:
        parsed = parse_tickers(tickers)
        companies = tuple(holding_company(ticker) for ticker in parsed)
    except ParseError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except KeyError as exc:
        typer.echo(f"error: unknown ticker {exc.args[0]}", err=True)
        raise typer.Exit(code=1) from exc

    if fixtures:
        root = output_dir if output_dir is not None else Path("data")
        batch: FixtureBatch | LiveBatch = FixtureBatch(FIXTURES)
    else:
        settings = get_settings()
        root = output_dir if output_dir is not None else settings.data_dir
        batch = LiveBatch(
            sec=HttpxJsonClient(
                user_agent=settings.sec_user_agent,
                limiter=shared_limiter(),
                timeout=120.0,
            ),
            fdic=HttpxJsonClient(user_agent=settings.sec_user_agent),
            clock=utc_now,
        )
    try:
        receipt = asyncio.run(run_extract(companies, batch, MetricsStore(root)))
    except MetricsInputError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except MetricsUnavailable as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    for company in receipt.companies:
        typer.echo(f"lead ticker={company.lead.company.ticker} cert={company.lead.cert}")
        typer.echo(
            f"xbrl ticker={company.xbrl.ticker} bytes={company.xbrl.metadata.bytes} "
            f"sha256={company.xbrl.metadata.sha256}"
        )
        typer.echo(
            f"financials cert={company.financials.cert} rows={company.financials.rows} "
            f"bytes={company.financials.metadata.bytes} "
            f"sha256={company.financials.metadata.sha256}"
        )
    typer.echo(
        f"metrics extract xbrl={len(receipt.companies)} "
        f"financials={len(receipt.companies)} output={root}"
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


@app.command("eval")
def eval_cmd(
    suite: str = typer.Option(..., "--suite", help="golden or graph."),
    small: bool = typer.Option(
        False,
        "--small",
        help="Fixture corpus, FakeEmbedder, in-memory Qdrant. No download.",
    ),
    input_dir: Path | None = typer.Option(
        None,
        "--input-dir",
        help="Root that contains raw/edgar. Forbidden with --small.",
    ),
    out: Path = typer.Option(
        Path("evals/results"),
        "--out",
        help="Directory for the versioned JSON and markdown table.",
    ),
    golden_path: Path = typer.Option(
        Path("evals/golden"),
        "--golden",
        help="Directory with v1.jsonl and passages/.",
    ),
    chunker: list[ChunkerName] | None = typer.Option(
        None,
        "--chunker",
        help="Repeatable. Defaults to section and fixed.",
    ),
    mode: list[str] | None = typer.Option(
        None,
        "--mode",
        help="Repeatable. dense, bm25, or hybrid. Defaults to all three.",
    ),
    embedder: EmbedderName | None = typer.Option(
        None,
        "--embedder",
        help="bge-small, minilm, or openai-small. Forbidden with --small.",
    ),
    k: int = typer.Option(5, "--k", help="Published cutoff. Retrieve still walks 10."),
    from_results: Path | None = typer.Option(
        None,
        "--from-results",
        help="Render a stored JSON matrix. Does not retrieve.",
    ),
    readme: Path | None = typer.Option(
        None,
        "--readme",
        help="Rewrite the README retrieval table from this run or --from-results.",
    ),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Committed twelve-filing Recall@5 snapshot. Forbidden with --small.",
    ),
    write_baseline_file: bool = typer.Option(
        False,
        "--write-baseline",
        help="Rewrite --baseline (or evals/baselines/nightly.json) from this run.",
    ),
    print_cache_key: bool = typer.Option(
        False,
        "--print-cache-key",
        help="Print the eval Qdrant cache key (config hash + accessions) and exit.",
    ),
) -> None:
    """Score handwritten gold with span-overlap recall@k, MRR, and nDCG.

    Full mode re-chunks raw filings, embeds them, and writes
    ``evals/results/<sha12>.json``. ``--small`` is the CI path.
    """
    if small and (baseline is not None or write_baseline_file):
        typer.echo("error: --small does not take --baseline", err=True)
        raise typer.Exit(code=1)
    if small and print_cache_key:
        typer.echo("error: --small does not take --print-cache-key", err=True)
        raise typer.Exit(code=1)
    if print_cache_key:
        if from_results is not None:
            typer.echo("error: --print-cache-key does not take --from-results", err=True)
            raise typer.Exit(code=1)
        settings = _eval_settings(False)
        source = input_dir if input_dir is not None else settings.data_dir
        chunkers = tuple(chunker) if chunker else (ChunkerName.SECTION, ChunkerName.FIXED)
        chosen_embedder = embedder if embedder is not None else EmbedderName.BGE_SMALL
        try:
            key = eval_index_cache_key(
                source,
                chunkers=chunkers,
                embedder=chosen_embedder,
                settings=settings,
            )
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(key)
        raise typer.Exit(code=0)
    if from_results is not None:
        try:
            reports = load_reports(from_results)
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        for stored in reports:
            typer.echo(render_markdown(stored), nl=False)
            if readme is not None:
                try:
                    update_readme(readme, stored)
                except ValueError as exc:
                    typer.echo(f"error: {exc}", err=True)
                    raise typer.Exit(code=1) from exc
            _gate_nightly(stored, baseline=baseline, write_baseline=write_baseline_file)
        return

    settings = _eval_settings(small)
    configure_logging(settings.log_level)
    configure_settings(settings)
    if suite == "graph":
        try:

            async def _graph() -> object:
                return await run_graph_suite(out_dir=out)

            graph_report = asyncio.run(_graph())
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        finally:
            shutdown()
        from capitalcite.eval.graph_eval import GraphEvalReport

        assert isinstance(graph_report, GraphEvalReport)
        typer.echo(
            "eval "
            f"suite=graph "
            f"n={graph_report.n} "
            f"fixture_substring_hit_rate={graph_report.fixture_substring_hit_rate:.3f} "
            f"graph_exact={graph_report.graph_exact:.3f} "
            f"delta={graph_report.delta:.3f} "
            f"out={out}"
        )
        typer.echo(render_graph_markdown(graph_report), nl=False)
        if readme is not None:
            try:
                update_readme_graph(readme, graph_report)
            except ValueError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
        return

    chunkers = tuple(chunker) if chunker else (ChunkerName.SECTION, ChunkerName.FIXED)
    report: EvalReport | None = None
    try:
        try:
            modes = _parse_modes(mode)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        async def _run() -> EvalReport:
            return await run_suite(
                suite=suite,
                small=small,
                input_dir=input_dir,
                out_dir=out,
                golden_dir=golden_path,
                chunkers=chunkers,
                modes=modes,
                embedder=embedder,
                k=k,
                settings=settings,
            )

        report = asyncio.run(_run())
    except EvalFailed as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except EvalUnverified as exc:
        typer.echo(f"unverified: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        shutdown()
    if report is None:
        raise typer.Exit(code=1)

    typer.echo(
        "eval "
        f"suite={report.suite} "
        f"small={str(report.small).lower()} "
        f"items={report.n_items} "
        f"arms={len(report.arms)} "
        f"out={out}"
    )
    typer.echo(render_markdown(report), nl=False)
    if readme is not None:
        try:
            update_readme(readme, report)
        except ValueError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    _gate_nightly(report, baseline=baseline, write_baseline=write_baseline_file)


@app.command("check")
def check_cmd(
    baseline: Path = typer.Option(
        DEFAULT_BASELINE,
        "--baseline",
        help="Committed snapshot. PRs fail when a gated rate drops past its item budget.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Optional directory for check.json.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Call OpenAI on one contract item. Skips as UNVERIFIED without OPENAI_API_KEY.",
    ),
    write_baseline_file: bool = typer.Option(
        False,
        "--write-baseline",
        help="Rewrite --baseline from this run. CI still requires the eval-baseline label.",
    ),
) -> None:
    """Fail if retrieval, contracts, or gate red-team regress against the baseline."""
    settings = _eval_settings(True)
    configure_logging(settings.log_level)
    configure_settings(settings)
    committed: Baseline | None = None
    if baseline.is_file():
        try:
            committed = load_baseline(baseline)
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    elif not write_baseline_file:
        typer.echo(f"error: missing baseline {baseline}", err=True)
        raise typer.Exit(code=1)
    seed = committed if committed is not None else _bootstrap_baseline()
    report: CheckReport | None = None
    try:

        async def _run() -> CheckReport:
            return await run_check(committed=seed, live=live)

        report = asyncio.run(_run())
    except CheckFailed as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except EvalFailed as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except EvalUnverified as exc:
        typer.echo(f"unverified: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    finally:
        shutdown()
    if report is None:
        raise typer.Exit(code=1)
    pack_ok = (
        report.redteam_failed == 0
        and all(case.ok for case in report.contracts)
        and report.live.kind != "failed"
    )
    if write_baseline_file:
        if not pack_ok:
            _echo_check_failures(report)
            typer.echo("error: refusing to write baseline from a failed pack", err=True)
            raise typer.Exit(code=1)
        write_baseline(baseline, report.snapshot)
        typer.echo(f"wrote baseline {baseline}")
        report = report.model_copy(update={"compare": compare(report.snapshot, report.snapshot)})
    if out is not None:
        from capitalcite.atomic import atomic_write_text

        out.mkdir(parents=True, exist_ok=True)
        atomic_write_text(out / "check.json", report.model_dump_json(indent=2) + "\n")
    live_status = report.live
    typer.echo(
        "check "
        f"ok={str(report.ok).lower()} "
        f"quote_sanity={report.snapshot.quote_sanity.hits}/"
        f"{report.snapshot.quote_sanity.items} "
        f"fixture={report.snapshot.fixture.hits}/{report.snapshot.fixture.items} "
        f"redteam_failed={report.redteam_failed} "
        f"live={live_status.kind}"
    )
    if live_status.kind == "unverified":
        typer.echo(f"unverified: {live_status.detail}", err=True)
    if not report.ok:
        _echo_check_failures(report)
        raise typer.Exit(code=1)


def _echo_check_failures(report: CheckReport) -> None:
    for failure in report.compare.failures:
        typer.echo(f"fail {failure.metric}: {failure.detail}", err=True)
    for case in report.contracts:
        if not case.ok:
            typer.echo(
                f"fail contract {case.id}: expected {case.expected} got {case.observed}",
                err=True,
            )
    if report.redteam_failed:
        typer.echo(f"fail redteam failed={report.redteam_failed}", err=True)
    if report.live.kind == "failed":
        typer.echo(f"fail live: {report.live.detail}", err=True)


def _bootstrap_baseline() -> Baseline:
    return Baseline(
        quote_sanity=TrackedRate(hits=0, items=0),
        fixture=TrackedRate(hits=0, items=0),
        small_n_items=0,
        small_arms=0,
        small_embedder_id="bootstrap",
        thresholds=Thresholds(),
    )


def _gate_nightly(
    report: EvalReport,
    *,
    baseline: Path | None,
    write_baseline: bool,
) -> None:
    if not write_baseline and baseline is None:
        return
    if report.small:
        typer.echo("error: --small does not take --baseline", err=True)
        raise typer.Exit(code=1)
    snapshot = snapshot_nightly(report)
    target = baseline if baseline is not None else DEFAULT_NIGHTLY
    if write_baseline:
        write_nightly(target, snapshot)
        typer.echo(f"wrote baseline {target}")
        committed = snapshot
    elif not target.is_file():
        typer.echo(f"error: missing baseline {target}", err=True)
        raise typer.Exit(code=1)
    else:
        try:
            committed = load_nightly(target)
        except (OSError, ValueError) as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
    compared = compare_nightly(snapshot, committed)
    typer.echo(
        "eval-baseline "
        f"ok={str(compared.ok).lower()} "
        f"arms={len(snapshot.arms)} "
        f"items={snapshot.n_items}"
    )
    if not compared.ok:
        for failure in compared.failures:
            typer.echo(f"fail {failure.metric}: {failure.detail}", err=True)
        raise typer.Exit(code=1)


def _eval_settings(small: bool) -> Settings:
    if small:
        return Settings(
            sec_user_agent="capitalcite-eval eval@example.com",
            chunker=ChunkerName.SECTION,
            chunk_size=512,
            chunk_overlap=64,
            embedder="bge-small",
            qdrant_url=":memory:",
        )
    return get_settings()


def _parse_modes(raw: list[str] | None) -> tuple[RetrievalMode, ...]:
    if not raw:
        return (RetrievalMode.DENSE, RetrievalMode.BM25, RetrievalMode.HYBRID)
    modes: list[RetrievalMode] = []
    for value in raw:
        try:
            modes.append(RetrievalMode(value))
        except ValueError as exc:
            raise ValueError(f"unknown mode {value!r}") from exc
    return tuple(modes)


def create_edgar_client(user_agent: str) -> EdgarClient:
    """Build the live EDGAR client. Tests patch this to inject a transport."""
    return EdgarClient(user_agent=user_agent)
