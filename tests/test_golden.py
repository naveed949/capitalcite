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
