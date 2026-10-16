"""Graph vs hybrid on questions that need entity traversal. Offline."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from capitalcite.atomic import atomic_write_text
from capitalcite.eval.runner import _git_sha
from capitalcite.kg import GraphQuery, GraphQueryKind, MemoryGraphStore, load_snapshot
from capitalcite.kg.snapshot import DEFAULT_SNAPSHOT

DEFAULT_GRAPH_ITEMS = Path("evals/golden/graph.jsonl")
_FIXTURE_CORPUS = Path("tests/fixtures/chunking/corpus")
GRAPH_BEGIN_MARK = "<!-- BEGIN GRAPH EVAL -->"
GRAPH_END_MARK = "<!-- END GRAPH EVAL -->"


class GraphGoldItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    kind: GraphQueryKind
    ticker: str | None = None
    rssd: str | None = None
    name: str | None = None
    period: str | None = None
    form: str | None = None
    expected_substrings: tuple[str, ...] = ()


class GraphItemScore(BaseModel):
    model_config = ConfigDict(frozen=True)
    item_id: str
    hybrid_hit: bool
    graph_exact: bool
    detail: str


class GraphEvalReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    suite: str = "graph"
    git_sha: str
    n: int = Field(ge=0)
    hybrid_hit_rate: float
    graph_exact: float
    delta: float
    items: tuple[GraphItemScore, ...]


def load_graph_items(path: Path | None = None) -> tuple[GraphGoldItem, ...]:
    target = path if path is not None else DEFAULT_GRAPH_ITEMS
    items: list[GraphGoldItem] = []
    for raw in target.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        items.append(GraphGoldItem.model_validate_json(raw))
    return tuple(items)


def _corpus_text(root: Path) -> str:
    parts: list[str] = []
    if not root.is_dir():
        return ""
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() in {".htm", ".html", ".txt"}:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _query_for(item: GraphGoldItem) -> GraphQuery:
    return GraphQuery(
        kind=item.kind,
        ticker=item.ticker,
        rssd=item.rssd,
        name=item.name,
        period=item.period,
        form=item.form,
    )


async def run_graph_suite(
    *,
    items_path: Path | None = None,
    snapshot_path: Path | None = None,
    corpus_dir: Path | None = None,
    out_dir: Path | None = None,
) -> GraphEvalReport:
    items = load_graph_items(items_path)
    if len(items) < 5:
        raise ValueError(f"graph suite needs at least 5 items, have {len(items)}")
    store = MemoryGraphStore()
    await store.load(load_snapshot(snapshot_path or DEFAULT_SNAPSHOT))
    corpus = _corpus_text(corpus_dir if corpus_dir is not None else _FIXTURE_CORPUS)
    scores: list[GraphItemScore] = []
    for item in items:
        expected = item.expected_substrings or (item.answer,)
        hybrid_hit = all(token in corpus for token in expected)
        result = await store.query(_query_for(item))
        graph_exact = result.ok and all(token in result.detail for token in expected)
        scores.append(
            GraphItemScore(
                item_id=item.id,
                hybrid_hit=hybrid_hit,
                graph_exact=graph_exact,
                detail=result.detail,
            )
        )
    await store.close()
    n = len(scores)
    hybrid_hit_rate = sum(row.hybrid_hit for row in scores) / n
    graph_rate = sum(row.graph_exact for row in scores) / n
    report = GraphEvalReport(
        git_sha=_git_sha(),
        n=n,
        hybrid_hit_rate=hybrid_hit_rate,
        graph_exact=graph_rate,
        delta=graph_rate - hybrid_hit_rate,
        items=tuple(scores),
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = report.git_sha[:12] + "-graph"
        atomic_write_text(out_dir / f"{stem}.json", report.model_dump_json(indent=2) + "\n")
        atomic_write_text(out_dir / f"{stem}.md", render_graph_markdown(report))
    return report


def render_graph_markdown(report: GraphEvalReport) -> str:
    lines = [
        f"suite=graph sha={report.git_sha} n={report.n} "
        f"hybrid_hit_rate={report.hybrid_hit_rate:.3f} "
        f"graph_exact={report.graph_exact:.3f} delta={report.delta:.3f}",
        "",
        "| System | n | Hit rate | Notes |",
        "|---|---:|---:|---|",
        f"| Hybrid on fixture HTML | {report.n} | {report.hybrid_hit_rate:.3f} | substring |",
        f"| graph_query (memory) | {report.n} | {report.graph_exact:.3f} | typed walk |",
        "",
        f"Delta (graph minus hybrid) = {report.delta:.3f}.",
        "Items need BHC→subsidiary or identifier joins. They are not in v1 recall.",
        "",
    ]
    return "\n".join(lines)


def update_readme_graph(readme: Path, report: GraphEvalReport) -> None:
    text = readme.read_text(encoding="utf-8")
    if GRAPH_BEGIN_MARK not in text or GRAPH_END_MARK not in text:
        raise ValueError(f"{readme} is missing {GRAPH_BEGIN_MARK} / {GRAPH_END_MARK}")
    pre, rest = text.split(GRAPH_BEGIN_MARK, 1)
    _, post = rest.split(GRAPH_END_MARK, 1)
    table = (
        "| System | n | Hit rate |\n"
        "|---|---:|---:|\n"
        f"| Hybrid (fixture corpus substrings) | {report.n} | {report.hybrid_hit_rate:.3f} |\n"
        f"| graph_query (memory snapshot) | {report.n} | {report.graph_exact:.3f} |\n"
        f"\nDelta (graph minus hybrid) = {report.delta:.3f}. "
        f"Generated from `evals/results/{report.git_sha[:12]}-graph.json`. "
        "Hybrid hit rate is 1 only when every expected substring is in the "
        "--small fixture HTML. Those files do not contain the RSSDs, so the "
        "rate is limited by the corpus text. "
        "These items need entity traversal. They are not in the 77-item filing-span matrix.\n"
    )
    atomic_write_text(readme, f"{pre}{GRAPH_BEGIN_MARK}\n\n{table}\n{GRAPH_END_MARK}{post}")
