from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

UNIT_SUFFIXES: tuple[str, ...] = ("ms", "MB", "seconds", "minutes", "years")
_UNIT_GROUP = "|".join(UNIT_SUFFIXES)

QUANTITY = re.compile(
    rf"""
    (?<![A-Za-z0-9_])
    (?P<lex>
        \d+/\d+
        | \d+\.\d+%
        | \d+%
        | \d+-[A-Za-z]+
        | \d+\.\d+[ \t]*(?:{_UNIT_GROUP})
        | \d+[ \t]*(?:{_UNIT_GROUP})
        | \d+\.\d+
        | \d+
    )
    (?![A-Za-z0-9_/])
    """,
    re.VERBOSE,
)

CHECKED_DOCS: tuple[str, ...] = ("docs/writeup.md",)
README_REL = "README.md"
BENCH_BEGIN_MARK = "<!-- BEGIN BENCH EVAL -->"
BENCH_END_MARK = "<!-- END BENCH EVAL -->"
ZERO_LEXEMES = frozenset({"0", "0.0", "0.00", "0.000"})
COST_LINE = re.compile(r"\bcost\b", re.IGNORECASE)
HEX_STEM = re.compile(r"^[0-9a-f]+", re.IGNORECASE)
RESULT_PATH = re.compile(r"evals/results/[A-Za-z0-9._-]+\.json")
MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
AUTOLINK = re.compile(r"https?://[^\s)>]+")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?\b")
YEAR_RANGE = re.compile(r"\b(?:19|20)\d{2}-(?:19|20)\d{2}\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
ADR_ID = re.compile(r"\bADR[ \t]*\d+\b", re.IGNORECASE)
ISSUE_ID = re.compile(r"#\d+")
LONG_HEX = re.compile(r"\b[0-9a-f]*[a-f][0-9a-f]{11,}\b", re.IGNORECASE)
DOTTED_VERSION = re.compile(r"\b\d+(?:\.\d+){2,}\b")
LEADING_ZERO = re.compile(r"\b0\d+\b")
AT_K = re.compile(r"@[0-9]+")
PERCENT_CI = re.compile(r"95%[ \t]*CI")


@dataclass(frozen=True)
class Span:
    path: str
    line: int
    column: int


@dataclass(frozen=True)
class CitedNumber:
    lexeme: str
    span: Span
    line_text: str


@dataclass(frozen=True)
class Ledger:
    readme: frozenset[str]
    projected: frozenset[str]

    def permits(self, cited: CitedNumber) -> bool:
        return cited.lexeme in self.readme or cited.lexeme in self.projected


@dataclass(frozen=True)
class Failure:
    span: Span
    message: str

    def render(self) -> str:
        return f"{self.span.path}:{self.span.line}:{self.span.column}: {self.message}"


def _blank(text: str, start: int, end: int) -> str:
    return text[:start] + (" " * (end - start)) + text[end:]


def _blank_matches(text: str, pattern: re.Pattern[str]) -> str:
    out = text
    for match in reversed(list(pattern.finditer(text))):
        out = _blank(out, match.start(), match.end())
    return out


def mask(text: str) -> str:
    out = text
    out = _blank_matches(out, FENCE)
    out = _blank_matches(out, HTML_COMMENT)
    for match in reversed(list(MD_LINK.finditer(out))):
        dest = match.group(2)
        dest_start = match.start(2) - 1
        dest_end = match.end(2) + 1
        out = _blank(out, dest_start, dest_end)
        if "unverified" in dest.lower():
            out = _blank(out, match.start(1), match.end(1))
    out = _blank_matches(out, AUTOLINK)
    out = _blank_matches(out, ISO_DATE)
    out = _blank_matches(out, YEAR_RANGE)
    out = _blank_matches(out, YEAR)
    out = _blank_matches(out, ADR_ID)
    out = _blank_matches(out, ISSUE_ID)
    out = _blank_matches(out, LONG_HEX)
    out = _blank_matches(out, DOTTED_VERSION)
    out = _blank_matches(out, LEADING_ZERO)
    out = _blank_matches(out, AT_K)
    out = _blank_matches(out, PERCENT_CI)
    return out


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return tuple(starts)


def _line_col(starts: tuple[int, ...], offset: int) -> tuple[int, int]:
    line = 1
    for index, start in enumerate(starts):
        if start > offset:
            break
        line = index + 1
    col = offset - starts[line - 1] + 1
    return line, col


def _norm_lexeme(raw: str) -> str:
    return re.sub(r"[ \t]+", " ", raw.strip())


def extract(text: str, rel: str) -> tuple[CitedNumber, ...]:
    masked = mask(text)
    starts = _line_starts(text)
    lines = text.splitlines()
    found: list[CitedNumber] = []
    for match in QUANTITY.finditer(masked):
        lexeme = _norm_lexeme(match.group("lex"))
        line, column = _line_col(starts, match.start("lex"))
        line_text = lines[line - 1] if 0 < line <= len(lines) else ""
        found.append(
            CitedNumber(
                lexeme=lexeme,
                span=Span(path=rel, line=line, column=column),
                line_text=line_text,
            )
        )
    return tuple(found)


def readme_lexemes(text: str) -> frozenset[str]:
    return frozenset(item.lexeme for item in extract(text, README_REL))


def linked_results(text: str) -> frozenset[str]:
    found: set[str] = set()
    for match in RESULT_PATH.finditer(text):
        path = match.group(0)
        if ".." in path:
            continue
        found.add(path)
    return frozenset(found)


def project_file(data: Mapping[str, object], readme: frozenset[str]) -> frozenset[str]:
    if data.get("suite") == "metrics":
        return _project_metrics(data, readme)
    if data.get("suite") == "bench":
        return _project_bench(data, readme)
    emitted: set[str] = set(_project_overhead(data))
    if data.get("small") is True:
        return frozenset(emitted)
    arms = data.get("arms")
    if not isinstance(arms, list):
        return frozenset(emitted)
    for arm in arms:
        if not isinstance(arm, dict):
            continue
        metrics = arm.get("metrics")
        if not isinstance(metrics, dict):
            continue
        items = metrics.get("items")
        misses = metrics.get("misses_at_5")
        if type(items) is int and isinstance(misses, list) and items > 0:
            hits = items - len(misses)
            if f"{hits / items:.3f}" in readme:
                emitted.add(f"{hits}/{items}")
        scores = arm.get("item_scores")
        if isinstance(scores, list):
            empty = 0
            late = 0
            for row in scores:
                if not isinstance(row, dict):
                    continue
                covered = row.get("covered_at")
                if isinstance(covered, list) and len(covered) == 0:
                    empty += 1
                first = row.get("first_rank")
                if type(first) is int and 6 <= first <= 10:
                    late += 1
            emitted.add(str(empty))
            emitted.add(str(late))
    return frozenset(emitted)


def _project_metrics(data: Mapping[str, object], readme: frozenset[str]) -> frozenset[str]:
    """Recount booleans. A stored rate float is not a source."""
    scores = data.get("scores")
    if not isinstance(scores, list):
        return frozenset()
    emitted: set[str] = set()
    for path in ("agent", "rag"):
        rows = [row for row in scores if isinstance(row, dict) and row.get("path") == path]
        values = [row for row in rows if row.get("cell") == "value"]
        nulls = [row for row in rows if row.get("cell") == "null"]
        exact_hits = sum(1 for row in values if row.get("exact") is True)
        refusal_hits = sum(1 for row in nulls if row.get("refusal") is True)
        _emit_rate(emitted, readme, exact_hits, len(values))
        _emit_rate(emitted, readme, refusal_hits, len(nulls))
    coverage = data.get("coverage")
    if isinstance(coverage, dict):
        covered = coverage.get("rows")
        if isinstance(covered, list):
            hits = sum(
                1 for row in covered if isinstance(row, dict) and row.get("disposition") == "CELL"
            )
            _emit_rate(emitted, readme, hits, len(covered))
    return frozenset(emitted)


def _project_bench(data: Mapping[str, object], readme: frozenset[str]) -> frozenset[str]:
    """Recount rates and percentiles. Stored floats are not a source."""
    queries = data.get("queries")
    if not isinstance(queries, list):
        return frozenset()
    emitted: set[str] = set()
    n_items = data.get("n_items")
    if type(n_items) is int:
        emitted.add(str(n_items))
    for arm in ("rag", "graph", "loop"):
        rows = [row for row in queries if isinstance(row, dict) and row.get("arm") == arm]
        recall_rows = [row for row in rows if isinstance(row.get("recall_at_5"), bool)]
        recall_hits = sum(1 for row in recall_rows if row.get("recall_at_5") is True)
        accurate = sum(1 for row in rows if row.get("accurate") is True)
        _emit_rate(emitted, readme, recall_hits, len(recall_rows))
        _emit_rate(emitted, readme, accurate, len(rows))
        totals = []
        compile_ms = []
        costs = []
        for row in rows:
            phases = row.get("phases")
            if isinstance(phases, dict):
                total = phases.get("total_ms")
                if isinstance(total, int | float):
                    totals.append(float(total))
                compile = phases.get("compile_ms")
                if isinstance(compile, int | float):
                    compile_ms.append(float(compile))
            cost = row.get("cost_usd")
            if isinstance(cost, int | float):
                costs.append(float(cost))
        if totals:
            _emit_ms(emitted, readme, _percentile(totals, 0.50))
            _emit_ms(emitted, readme, _percentile(totals, 0.95))
            _emit_ms(emitted, readme, sum(totals) / len(totals))
        if compile_ms:
            _emit_ms(emitted, readme, sum(compile_ms) / len(compile_ms))
        if costs:
            _emit_cost(emitted, readme, sum(costs) / len(costs))
    for key in (
        "recall_delta_graph_minus_loop",
        "accuracy_delta_graph_minus_loop",
        "accuracy_delta_graph_minus_rag",
    ):
        _emit_ci(emitted, readme, data.get(key))
    recall = data.get("recall")
    if isinstance(recall, dict):
        _emit_ci(emitted, readme, recall.get("graph_minus_loop"))
        _emit_ci(emitted, readme, recall.get("graph_minus_rag"))
    return frozenset(emitted)


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (len(xs) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def _emit_ms(emitted: set[str], readme: frozenset[str], value: float) -> None:
    candidates = (
        f"{value:.1f}",
        f"{value:.1f} ms",
        f"{round(value)}",
        f"{round(value)} ms",
    )
    for lexeme in candidates:
        if lexeme in readme:
            emitted.add(lexeme)


def _emit_cost(emitted: set[str], readme: frozenset[str], value: float) -> None:
    candidates = (f"{value:.6f}", f"{value:.3f}", f"{value:.1f}", "0")
    for lexeme in candidates:
        if lexeme in readme:
            emitted.add(lexeme)


def _emit_ci(emitted: set[str], readme: frozenset[str], blob: object) -> None:
    if not isinstance(blob, Mapping):
        return
    for key in ("point", "lo", "hi"):
        value = blob.get(key)
        if isinstance(value, int | float):
            lexeme = f"{value:.3f}"
            if lexeme in readme:
                emitted.add(lexeme)
    for key in ("B", "seed", "n"):
        value = blob.get(key)
        if type(value) is int:
            emitted.add(str(value))


def _emit_rate(emitted: set[str], readme: frozenset[str], hits: int, items: int) -> None:
    if items <= 0:
        return
    rate = f"{hits / items:.3f}"
    if rate in readme:
        emitted.add(f"{hits}/{items}")
        emitted.add(rate)


def _project_overhead(data: Mapping[str, object]) -> frozenset[str]:
    cited = data.get("cited")
    if not isinstance(cited, dict):
        return frozenset()
    return frozenset(value for value in cited.values() if isinstance(value, str))


def _stem_hex(path: Path) -> str | None:
    match = HEX_STEM.match(path.name)
    if match is None:
        return None
    return match.group(0).lower()


def build_ledger(root: Path, writeup: str, readme: str) -> tuple[Ledger, tuple[Failure, ...]]:
    readme_set = readme_lexemes(readme)
    paths = linked_results(writeup) & linked_results(readme)
    projected: set[str] = set()
    failures: list[Failure] = []
    for rel in sorted(paths):
        if "unverified" in Path(rel).name.lower():
            continue
        full = root / rel
        if not full.is_file():
            failures.append(Failure(Span(rel, 0, 0), f"linked {rel} is missing"))
            continue
        raw = full.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            failures.append(Failure(Span(rel, 0, 0), f"linked {rel} is not JSON"))
            continue
        if not isinstance(payload, dict):
            continue
        expected = _stem_hex(Path(rel))
        git_sha = payload.get("git_sha")
        if (
            expected is not None
            and isinstance(git_sha, str)
            and not git_sha.lower().startswith(expected)
        ):
            continue
        projected.update(project_file(payload, readme_set))
    return Ledger(readme=readme_set, projected=frozenset(projected)), tuple(failures)


def _readme_bench_block(text: str) -> str | None:
    if BENCH_BEGIN_MARK not in text or BENCH_END_MARK not in text:
        return None
    _, rest = text.split(BENCH_BEGIN_MARK, 1)
    block, _ = rest.split(BENCH_END_MARK, 1)
    return block


def _check_readme_bench_block(readme: str, projected: frozenset[str]) -> tuple[Failure, ...]:
    """README bench table numbers must come from committed results, not the rest of the README."""
    block = _readme_bench_block(readme)
    if block is None:
        return ()
    prefix, _ = readme.split(BENCH_BEGIN_MARK, 1)
    line_offset = prefix.count("\n")
    failures: list[Failure] = []
    for cited in extract(block, README_REL):
        span = Span(cited.span.path, cited.span.line + line_offset, cited.span.column)
        if cited.lexeme in ZERO_LEXEMES and COST_LINE.search(cited.line_text):
            failures.append(
                Failure(
                    span,
                    f"'{cited.lexeme}' is on a cost line, and cost is UNVERIFIED",
                )
            )
            continue
        if cited.lexeme in projected:
            continue
        failures.append(
            Failure(
                span,
                f"'{cited.lexeme}' in the README bench block is not in linked evals/results JSON",
            )
        )
    return tuple(failures)


def judge(cited: CitedNumber, ledger: Ledger) -> Failure | None:
    if cited.lexeme in ZERO_LEXEMES and COST_LINE.search(cited.line_text):
        return Failure(
            cited.span,
            f"'{cited.lexeme}' is on a cost line, and cost is UNVERIFIED",
        )
    if ledger.permits(cited):
        return None
    return Failure(
        cited.span,
        f"'{cited.lexeme}' is not in README.md or linked evals/results JSON",
    )


def parse_root(argv: list[str]) -> Path | Failure:
    if argv == []:
        return Path(__file__).resolve().parent.parent
    if len(argv) == 2 and argv[0] == "--root":
        return Path(argv[1]).resolve()
    return Failure(
        Span("scripts/check_writeup_numbers.py", 0, 0),
        "usage: python scripts/check_writeup_numbers.py [--root PATH]",
    )


def check(root: Path) -> tuple[Failure, ...]:
    failures: list[Failure] = []
    readme_path = root / README_REL
    texts: dict[str, str] = {}
    if not readme_path.is_file():
        failures.append(Failure(Span(README_REL, 0, 0), "missing file"))
        readme_text = ""
    else:
        readme_text = readme_path.read_text(encoding="utf-8")
        texts[README_REL] = readme_text
    for rel in CHECKED_DOCS:
        path = root / rel
        if not path.is_file():
            failures.append(Failure(Span(rel, 0, 0), "missing file"))
        else:
            texts[rel] = path.read_text(encoding="utf-8")
    writeup_text = texts.get("docs/writeup.md", "")
    if README_REL in texts and "docs/writeup.md" in texts:
        ledger, link_failures = build_ledger(root, writeup_text, readme_text)
        failures.extend(link_failures)
        failures.extend(_check_readme_bench_block(readme_text, ledger.projected))
    elif README_REL in texts:
        ledger = Ledger(readme=readme_lexemes(readme_text), projected=frozenset())
        failures.extend(_check_readme_bench_block(readme_text, ledger.projected))
    else:
        ledger = Ledger(readme=frozenset(), projected=frozenset())
    for rel in CHECKED_DOCS:
        if rel not in texts:
            continue
        for cited in extract(texts[rel], rel):
            hit = judge(cited, ledger)
            if hit is not None:
                failures.append(hit)
    return tuple(failures)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    parsed = parse_root(args)
    if isinstance(parsed, Failure):
        print(parsed.render())
        return 1
    failures = check(parsed)
    if failures:
        for item in failures:
            print(item.render())
        return 1
    cited = 0
    for rel in CHECKED_DOCS:
        path = parsed / rel
        if path.is_file():
            cited += len(extract(path.read_text(encoding="utf-8"), rel))
    print(f"writeup-numbers ok files={len(CHECKED_DOCS)} cited={cited}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
