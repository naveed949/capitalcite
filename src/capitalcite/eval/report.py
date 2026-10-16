"""Markdown table and README patch from an EvalReport."""

from __future__ import annotations

from pathlib import Path

from capitalcite.atomic import atomic_write_text
from capitalcite.eval.models import ArmResult, EvalReport, ItemScore

BEGIN_MARK = "<!-- BEGIN RETRIEVAL EVAL -->"
END_MARK = "<!-- END RETRIEVAL EVAL -->"
AGENT_BEGIN_MARK = "<!-- BEGIN AGENT RAG EVAL -->"
AGENT_END_MARK = "<!-- END AGENT RAG EVAL -->"


def render_markdown(report: EvalReport) -> str:
    lines = [
        f"suite={report.suite} sha={report.git_sha} small={str(report.small).lower()} "
        f"items={report.n_items} k={report.k} embedder={report.embedder_id} "
        f"sparse={report.sparse_id or 'absent'} "
        f"fastembed={report.fastembed_version or 'absent'} "
        f"qdrant-client={report.qdrant_client_version or 'absent'}",
        "",
        "| config_hash | chunker | embedder | mode | n | Recall@5 | MRR@5 | nDCG@5 "
        "| Recall@10 | MRR@10 | nDCG@10 | misses@5 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for arm in report.arms:
        m = arm.metrics
        misses = _miss_cell(m.misses_at_5)
        lines.append(
            "| "
            + " | ".join(
                (
                    arm.config_hash[:12],
                    arm.chunker.value,
                    arm.embedder_id,
                    arm.mode.value,
                    str(m.items),
                    f"{m.recall_at_5:.3f}",
                    f"{m.mrr_at_5:.3f}",
                    f"{m.ndcg_at_5:.3f}",
                    f"{m.recall_at_10:.3f}",
                    f"{m.mrr_at_10:.3f}",
                    f"{m.ndcg_at_10:.3f}",
                    misses,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            *_miss_lines(report),
            "",
            "A hit is relevant when chunk.doc_id matches and the half-open spans overlap.",
            "Recall@k is the fraction of items with any relevant hit in the top k.",
            "MRR@k is the mean of 1/rank of the first relevant hit (0 if past k).",
            "nDCG@k credits each gold span once, at its first covering hit, and is clamped at 1.",
            f"Noise floor: n={report.n_items}, one item is {1 / report.n_items:.3f} recall."
            if report.n_items
            else "n=0 so a one-item move is undefined.",
            "RAGAS context precision/recall and hybrid+rerank are UNVERIFIED.",
            _probe_line(report),
            *_agent_vs_rag_lines(report),
        ]
    )
    return "\n".join(lines) + "\n"


def write_report(report: EvalReport, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _stem(report)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    atomic_write_text(json_path, report.model_dump_json(indent=2) + "\n")
    atomic_write_text(md_path, render_markdown(report))
    return json_path, md_path


def load_report(path: Path) -> EvalReport:
    return EvalReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_reports(source: Path) -> tuple[EvalReport, ...]:
    if source.is_file():
        return (load_report(source),)
    files = sorted(source.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no eval JSON under {source}")
    return tuple(load_report(path) for path in files)


def update_readme(readme: Path, report: EvalReport) -> None:
    text = readme.read_text(encoding="utf-8")
    if BEGIN_MARK not in text or END_MARK not in text:
        raise ValueError(f"{readme} is missing {BEGIN_MARK} / {END_MARK}")
    pre, rest = text.split(BEGIN_MARK, 1)
    _, post = rest.split(END_MARK, 1)
    table = _readme_table(report)
    text = f"{pre}{BEGIN_MARK}\n\n{table}\n{END_MARK}{post}"
    if (
        report.agent_vs_rag is not None
        and not report.small
        and AGENT_BEGIN_MARK in text
        and AGENT_END_MARK in text
    ):
        pre, rest = text.split(AGENT_BEGIN_MARK, 1)
        _, post = rest.split(AGENT_END_MARK, 1)
        text = f"{pre}{AGENT_BEGIN_MARK}\n\n{_agent_readme_table(report)}\n{AGENT_END_MARK}{post}"
    atomic_write_text(readme, text)


def _readme_table(report: EvalReport) -> str:
    lines = [
        "| Chunking | Embedding | Retrieval | n | Recall@5 | MRR@5 | nDCG@5 "
        "| Recall@10 | MRR@10 | nDCG@10 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in report.arms:
        m = arm.metrics
        lines.append(
            "| "
            + " | ".join(
                (
                    arm.chunker.value,
                    arm.embedder_id,
                    arm.mode.value,
                    str(m.items),
                    f"{m.recall_at_5:.3f}",
                    f"{m.mrr_at_5:.3f}",
                    f"{m.ndcg_at_5:.3f}",
                    f"{m.recall_at_10:.3f}",
                    f"{m.mrr_at_10:.3f}",
                    f"{m.ndcg_at_10:.3f}",
                )
            )
            + " |"
        )
    lines.append(
        "| section | — | hybrid + rerank | — | UNVERIFIED | UNVERIFIED | UNVERIFIED "
        "| UNVERIFIED | UNVERIFIED | UNVERIFIED |"
    )
    lines.append("")
    lines.append(
        f"Generated from `evals/results/{_stem(report)}.json` "
        f"(sha `{report.git_sha[:12]}`, embedder `{report.embedder_id}`, "
        f"sparse `{report.sparse_id or 'absent'}`, "
        f"fastembed `{report.fastembed_version or 'absent'}`, "
        f"qdrant-client `{report.qdrant_client_version or 'absent'}`). "
        f"Noise floor: n={report.n_items}, one item is "
        f"{(1 / report.n_items) if report.n_items else 0:.3f} recall. "
        "RAGAS context precision/recall is UNVERIFIED (no LLM key). "
        "minilm is UNVERIFIED (a second full embed is not cheap on this corpus)."
    )
    return "\n".join(lines) + "\n"


def _agent_vs_rag_lines(report: EvalReport) -> list[str]:
    row = report.agent_vs_rag
    if row is None:
        return []
    return [
        "",
        "Agent vs RAG retrieval on comparison and multi-hop items "
        f"(n={row.n}, {row.chunker.value}/{row.mode.value}, {row.label}).",
        f"Plain RAG Recall@5={row.rag_recall_at_5:.3f} Recall@10={row.rag_recall_at_10:.3f}.",
        f"Agent Recall@5={row.agent_recall_at_5:.3f} Recall@10={row.agent_recall_at_10:.3f}.",
        "Answer quality stays UNVERIFIED.",
    ]


def _agent_readme_table(report: EvalReport) -> str:
    row = report.agent_vs_rag
    if row is None:
        return (
            "| System | n | Recall@5 | Recall@10 | Planner |\n"
            "|---|---:|---:|---:|---|\n"
            "| Plain RAG (section/hybrid) | — | UNVERIFIED | UNVERIFIED | — |\n"
            "| Agent | — | UNVERIFIED | UNVERIFIED | fake/heuristic planner |\n"
            "\n"
            "Comparison and multi-hop handwritten items only. "
            "Answer quality stays UNVERIFIED.\n"
        )
    return (
        "| System | n | Recall@5 | Recall@10 | Planner |\n"
        "|---|---:|---:|---:|---|\n"
        f"| Plain RAG ({row.chunker.value}/{row.mode.value}) | {row.n} | "
        f"{row.rag_recall_at_5:.3f} | {row.rag_recall_at_10:.3f} | — |\n"
        f"| Agent | {row.n} | {row.agent_recall_at_5:.3f} | "
        f"{row.agent_recall_at_10:.3f} | {row.label} |\n"
        "\n"
        "Comparison and multi-hop handwritten items only. "
        "Answer quality stays UNVERIFIED.\n"
    )


def _stem(report: EvalReport) -> str:
    suffix = "-small" if report.small else ""
    return f"{report.git_sha[:12]}{suffix}"


def _miss_lines(report: EvalReport) -> list[str]:
    lines = ["Misses@5"]
    for arm in report.arms:
        misses = arm.metrics.misses_at_5
        if len(misses) > 12:
            shown = ", ".join(misses[:8]) + f", ... (+{len(misses) - 8})"
        else:
            shown = ", ".join(misses) if misses else "(none)"
        lines.append(f"- {arm.chunker.value}/{arm.mode.value}: {shown}")
    return lines


def _miss_cell(misses: tuple[str, ...]) -> str:
    if not misses:
        return ""
    if len(misses) <= 4:
        return ", ".join(misses)
    return f"{len(misses)} (see JSON)"


def _probe_line(report: EvalReport) -> str:
    notes: list[str] = []
    for arm in report.arms:
        if arm.mode.value != "hybrid":
            continue
        score = _item(arm, "cc-h-013")
        if score is None:
            continue
        rank = score.first_rank
        label = "absent" if rank is None else f"rank {rank}"
        notes.append(f"{arm.chunker.value}/{arm.mode.value} cc-h-013 {label}")
    if not notes:
        return "cc-h-013 was not in this suite."
    return "Probe: " + "; ".join(notes) + "."


def _item(arm: ArmResult, item_id: str) -> ItemScore | None:
    for score in arm.item_scores:
        if score.item_id == item_id:
            return score
    return None
