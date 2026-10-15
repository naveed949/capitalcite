from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from capitalcite.cli import app
from capitalcite.golden.models import Evidence, EvidenceRole, GoldenItem, ItemSource, QuestionType
from capitalcite.golden.store import (
    DEFAULT_GOLDEN_DIR,
    ITEMS_NAME,
    ValidationError,
    load_golden_set,
    validate_golden_set,
)

runner = CliRunner()
REQUIRED_FIELDS = {"id", "question", "answer", "evidence", "type", "answerable"}


def test_committed_set_validates() -> None:
    stats = validate_golden_set(DEFAULT_GOLDEN_DIR)
    assert stats.items >= 90
    assert stats.unanswerable_or_trick_pct >= 0.15
    assert stats.numeric_pct >= 0.30
    assert stats.financebench >= 1
    assert stats.finqa >= 1


def test_jsonl_has_required_fields() -> None:
    path = DEFAULT_GOLDEN_DIR / ITEMS_NAME
    raw = path.read_text(encoding="utf-8").splitlines()[0]
    record = json.loads(raw)
    assert set(record) >= REQUIRED_FIELDS
    assert isinstance(record["evidence"], list)
    if record["answerable"]:
        assert record["evidence"]
        assert {"doc_id", "section", "quote"} <= set(record["evidence"][0])


def test_every_quote_is_in_its_passage() -> None:
    golden = load_golden_set(DEFAULT_GOLDEN_DIR)
    for item in golden.items:
        for row in item.evidence:
            key = row.doc_id.replace("/", "__")
            assert row.quote in golden.passages[key]


def test_answerable_handwritten_uses_ingest_accessions() -> None:
    golden = load_golden_set(DEFAULT_GOLDEN_DIR)
    accessions = {
        "0000019617-24-000225",
        "0000019617-25-000270",
        "0001628280-26-008131",
        "0000070858-24-000122",
        "0000070858-25-000139",
        "0000070858-26-000157",
        "0000072971-24-000064",
        "0000072971-25-000066",
        "0000072971-26-000133",
        "0000831001-24-000033",
        "0000831001-25-000067",
        "0000831001-26-000011",
    }
    handwritten = [
        item for item in golden.items if item.source is ItemSource.HANDWRITTEN and item.answerable
    ]
    assert handwritten
    for item in handwritten:
        for row in item.evidence:
            assert row.doc_id in accessions


def test_ungrounded_quote_fails(tmp_path: Path) -> None:
    items_path = tmp_path / ITEMS_NAME
    passage_dir = tmp_path / "passages"
    passage_dir.mkdir()
    passage_dir.joinpath("0000019617-25-000270.txt").write_text(
        "# 0000019617-25-000270\n## Item 7\nCET1 capital was $275.5 billion.\n",
        encoding="utf-8",
    )
    fake = GoldenItem(
        id="cc-test-1",
        question="What was CET1?",
        answer="99%",
        evidence=[
            Evidence(
                doc_id="0000019617-25-000270",
                section="Item 7",
                quote="CET1 capital was $99 billion.",
                role=EvidenceRole.ANSWER,
            )
        ],
        type=QuestionType.NUMERIC,
        answerable=True,
    )
    items_path.write_text(fake.model_dump_json() + "\n", encoding="utf-8")
    try:
        validate_golden_set(tmp_path)
    except ValidationError as exc:
        assert "quote not found" in str(exc)
    else:
        raise AssertionError("expected ValidationError")


def test_cli_golden_validate() -> None:
    result = runner.invoke(app, ["golden", "validate", "--path", "evals/golden"])
    assert result.exit_code == 0, result.output
    assert "items=" in result.stdout
    assert "numeric_pct=" in result.stdout


def test_cli_help_lists_golden() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "golden" in result.stdout


def test_handwritten_evidence_has_spans_and_benchmarks_do_not() -> None:
    golden = load_golden_set(DEFAULT_GOLDEN_DIR)
    handwritten = 0
    for item in golden.items:
        if item.source is ItemSource.HANDWRITTEN:
            handwritten += 1
            assert item.in_recall
            for row in item.evidence:
                assert row.start is not None
                assert row.end is not None
                assert row.end > row.start
        else:
            assert not item.in_recall
            for row in item.evidence:
                assert row.start is None
                assert row.end is None
    assert handwritten >= 80


def test_against_raw_aligns_fixture_quote() -> None:
    from capitalcite.golden.raw import validate_against_raw
    from capitalcite.parse import CitationSource, parse_path
    from capitalcite.parse.sections import detect_sections, section_at

    raw = Path(
        "tests/fixtures/chunking/corpus/raw/edgar/JPM/10-K/"
        "2024-12-31/0000019617-25-000270/jpm-20241231.htm"
    )
    parsed = parse_path(
        raw,
        CitationSource(
            doc_id="0000019617-25-000270",
            source_url="https://www.sec.gov/Archives/edgar/data/19617/000001961725000270/jpm-20241231.htm",
        ),
    )
    quote = "CET1 ratio"
    start = parsed.text.find(quote)
    assert start >= 0
    section = section_at(detect_sections(parsed.text), start)
    item = GoldenItem(
        id="cc-test-raw",
        question="What capital ratio appears in the fixture table?",
        answer="CET1 ratio",
        evidence=[
            Evidence(
                doc_id="0000019617-25-000270",
                section=section,
                quote=quote,
                role=EvidenceRole.ANSWER,
                start=start,
                end=start + len(quote),
            )
        ],
        type=QuestionType.EXTRACTIVE,
        answerable=True,
    )
    report = validate_against_raw([item], Path("tests/fixtures/chunking/corpus"))
    assert report.ok
    assert report.aligned == 1
    assert report.skipped_benchmark == 0


def test_cli_against_raw_requires_ingest_tree(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "golden",
            "validate",
            "--path",
            "evals/golden",
            "--against-raw",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "missing ingest tree" in result.output


def test_handwritten_missing_span_fails(tmp_path: Path) -> None:
    items_path = tmp_path / ITEMS_NAME
    passage_dir = tmp_path / "passages"
    passage_dir.mkdir()
    passage_dir.joinpath("0000019617-25-000270.txt").write_text(
        "# 0000019617-25-000270\n## Item 7\nCET1 capital was $275.5 billion.\n",
        encoding="utf-8",
    )
    item = GoldenItem(
        id="cc-test-span",
        question="What was CET1 capital?",
        answer="$275.5 billion",
        evidence=[
            Evidence(
                doc_id="0000019617-25-000270",
                section="Item 7",
                quote="CET1 capital was $275.5 billion.",
                role=EvidenceRole.ANSWER,
            )
        ],
        type=QuestionType.NUMERIC,
        answerable=True,
    )
    items_path.write_text(item.model_dump_json() + "\n", encoding="utf-8")
    try:
        validate_golden_set(tmp_path)
    except ValidationError as exc:
        assert "needs start/end" in str(exc)
    else:
        raise AssertionError("expected ValidationError")


def test_cli_validate_help_lists_against_raw() -> None:
    result = runner.invoke(app, ["golden", "validate", "--help"])
    assert result.exit_code == 0
    assert "--against-raw" in result.stdout
    assert "--path" in result.stdout
