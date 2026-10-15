from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capitalcite.chunking import chunk_document
from capitalcite.chunking.models import Chunk, ChunkerName
from capitalcite.chunking.service import run_chunk
from capitalcite.cli import app
from capitalcite.config import Settings
from capitalcite.ingest.store import FilingStore
from capitalcite.parse import CitationSource, ParsedDocument, parse_path, pdf_to_document
from capitalcite.parse.sections import detect_sections

_CORPUS = Path(__file__).parent / "fixtures" / "chunking" / "corpus"
_JPM = _CORPUS / "raw" / "edgar" / "JPM" / "10-K" / "2024-12-31"
FIXTURE = _JPM / "0000019617-25-000270" / "jpm-20241231.htm"
LONG_TABLE_FIXTURE = _JPM / "0000019617-25-000271" / "jpm-item7-table.htm"

SOURCE = CitationSource(
    doc_id="0000019617-25-000270",
    source_url=(
        "https://www.sec.gov/Archives/edgar/data/19617/000001961725000270/jpm-20241231.htm"
    ),
    ticker="JPM",
    form="10-K",
    period="2024-12-31",
    accession="0000019617-25-000270",
)
TABLE_SOURCE = CitationSource(
    doc_id="0000019617-25-000271",
    source_url=(
        "https://www.sec.gov/Archives/edgar/data/19617/000001961725000271/jpm-item7-table.htm"
    ),
    ticker="JPM",
    form="10-K",
    period="2024-12-31",
    accession="0000019617-25-000271",
)
_TABLE_ROW = re.compile(r"^\|.+\|$")
_TABLE_SEP = re.compile(r"^\|\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|$")

runner = CliRunner()


def _parsed() -> ParsedDocument:
    return parse_path(FIXTURE, SOURCE)


def _table_parsed() -> ParsedDocument:
    return parse_path(LONG_TABLE_FIXTURE, TABLE_SOURCE)


def _long_parsed() -> ParsedDocument:
    paragraphs = [f"Paragraph {index}. " + ("lorem " * 50) for index in range(40)]
    text = "Item 7. MD&A\n\n" + "\n\n".join(paragraphs) + "\n\nItem 8. Statements\nNotes follow."
    return ParsedDocument(
        source=SOURCE,
        text=text,
        media_type="html",
        sections=detect_sections(text),
        pages=(),
        tables=(),
    )


def _markdown_tables(text: str) -> list[str]:
    tables: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _TABLE_ROW.match(line.strip()):
            current.append(line.strip())
            continue
        if current:
            tables.append("\n".join(current))
            current = []
    if current:
        tables.append("\n".join(current))
    return tables


def _assert_valid_table(block: str) -> None:
    rows = block.splitlines()
    assert len(rows) >= 3
    assert _TABLE_ROW.match(rows[0])
    assert _TABLE_SEP.match(rows[1])
    assert all(_TABLE_ROW.match(row) for row in rows)
    assert all(row.strip() for row in rows)
    assert "" not in rows


def _assert_verbatim(document: ParsedDocument, chunk: Chunk) -> None:
    original = document.text[chunk.start : chunk.end]
    expected = f"{chunk.header_prefix}{original}" if chunk.header_prefix else original
    assert chunk.text == expected
    if original.strip():
        leading = len(original) - len(original.lstrip())
        assert chunk.offset == chunk.start + leading
        assert document.text[chunk.offset] == original.lstrip()[0]
    else:
        assert chunk.offset == chunk.start
    if chunk.header_prefix:
        assert chunk.header_prefix in document.text
        assert chunk.text.startswith(chunk.header_prefix)


def test_html_tables_become_markdown() -> None:
    document = _parsed()
    assert document.tables
    table = document.tables[0]
    assert "| Metric | 2024 | 2025 |" in table
    assert "| CET1 ratio | 15.1% | 16.0% |" in table
    assert "CET1 ratio" in document.text
    assert "<table>" not in document.text


def test_section_boundaries_skip_toc() -> None:
    document = _parsed()
    names = [span.name for span in document.sections]
    assert names == ["body", "Item 1", "Item 7", "Item 8"]
    item7 = next(span for span in document.sections if span.name == "Item 7")
    item8 = next(span for span in document.sections if span.name == "Item 8")
    assert "CET1 ratio" in document.text[item7.start : item7.end]
    assert "consolidated financial statements" in document.text[item8.start : item8.end]
    assert "..... 12" not in document.text[item7.start : item7.end]


def test_detect_sections_on_bare_text() -> None:
    text = "Preface\nItem 7. MD&A\nCash is up.\nItem 8. Statements\nNotes follow."
    names = [span.name for span in detect_sections(text)]
    assert names == ["body", "Item 7", "Item 8"]


def test_part_prefix_item_headings() -> None:
    text = "Preface\nPart II — Item 7. MD&A\nCash is up.\nItem 8. Statements\nNotes follow."
    spans = detect_sections(text)
    assert [span.name for span in spans] == ["body", "Item 7", "Item 8"]
    item7 = next(span for span in spans if span.name == "Item 7")
    assert text[item7.start : item7.end].startswith("Part II — Item 7")


def test_item_see_page_is_not_a_heading() -> None:
    text = (
        "Item 1. Business\nWe are a bank.\nItem 7. See page 45\n"
        "Still the business section.\nPart II — Item 7. MD&A\nCash is up."
    )
    spans = detect_sections(text)
    assert [span.name for span in spans] == ["Item 1", "Item 7"]
    item1 = next(span for span in spans if span.name == "Item 1")
    assert "Item 7. See page 45" in text[item1.start : item1.end]
    item7 = next(span for span in spans if span.name == "Item 7")
    assert "Cash is up." in text[item7.start : item7.end]


def test_section_chunks_carry_citation_fields() -> None:
    document = _parsed()
    chunks, stats = chunk_document(document, chunker=ChunkerName.SECTION, size=512, overlap=64)
    assert stats.count == len(chunks)
    assert stats.chunker == ChunkerName.SECTION
    assert stats.mean_tokens == pytest.approx(sum(c.token_count for c in chunks) / len(chunks))
    by_section = {chunk.section: chunk for chunk in chunks}
    assert "Item 7" in by_section
    assert "Item 8" in by_section
    assert "CET1 ratio" in by_section["Item 7"].text
    assert "| 15.1% |" in by_section["Item 7"].text
    for chunk in chunks:
        assert chunk.doc_id == SOURCE.doc_id
        assert chunk.source_url == SOURCE.source_url
        assert chunk.section
        assert chunk.offset >= 0
        assert chunk.start <= chunk.offset <= chunk.end
        assert chunk.page is None
        assert chunk.text
        _assert_verbatim(document, chunk)


def test_chunker_registry_picks_each_strategy() -> None:
    document = _parsed()
    for name in (ChunkerName.FIXED, ChunkerName.RECURSIVE, ChunkerName.SECTION):
        chunks, stats = chunk_document(document, chunker=name, size=32, overlap=8)
        assert stats.chunker == name
        assert stats.count == len(chunks)
        assert chunks
        assert all(chunk.chunker == name for chunk in chunks)
        assert all(chunk.doc_id == SOURCE.doc_id for chunk in chunks)


def test_settings_selects_chunker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("CHUNKER", "fixed")
    monkeypatch.setenv("CHUNK_SIZE", "256")
    monkeypatch.setenv("CHUNK_OVERLAP", "32")
    settings = Settings()
    assert settings.chunker == ChunkerName.FIXED
    assert settings.chunk_size == 256
    assert settings.chunk_overlap == 32


def test_settings_rejects_overlap_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "capitalcite-tests tests@example.com")
    monkeypatch.setenv("CHUNK_SIZE", "64")
    monkeypatch.setenv("CHUNK_OVERLAP", "64")
    with pytest.raises(Exception, match="CHUNK_OVERLAP"):
        Settings()


def test_run_chunk_writes_jsonl(tmp_path: Path) -> None:
    corpus = _CORPUS
    summary = run_chunk(
        input_root=corpus,
        output=FilingStore(tmp_path),
        chunker=ChunkerName.SECTION,
        size=512,
        overlap=64,
    )
    assert summary.files == 2
    assert summary.chunks >= 3
    chunks_path = (
        tmp_path
        / "parsed"
        / "edgar"
        / "JPM"
        / "10-K"
        / "2024-12-31"
        / "0000019617-25-000270"
        / "chunks.jsonl"
    )
    rows = [
        json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert {row["section"] for row in rows} >= {"Item 7", "Item 8"}
    item7 = next(row for row in rows if row["section"] == "Item 7")
    assert item7["doc_id"] == "0000019617-25-000270"
    assert "sec.gov/Archives/edgar/data/" in item7["source_url"]
    assert item7["offset"] >= 0
    assert item7["start"] <= item7["offset"] <= item7["end"]
    assert "CET1 ratio" in item7["text"]


def test_cli_chunk_fixture(tmp_path: Path) -> None:
    corpus = _CORPUS
    result = runner.invoke(
        app,
        [
            "chunk",
            "--input-dir",
            str(corpus),
            "--output-dir",
            str(tmp_path),
            "--chunker",
            "section",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "strategy=section" in result.stdout
    assert "files=2" in result.stdout
    stats = json.loads(
        (
            tmp_path
            / "parsed"
            / "edgar"
            / "JPM"
            / "10-K"
            / "2024-12-31"
            / "0000019617-25-000270"
            / "stats.json"
        ).read_text(encoding="utf-8")
    )
    assert stats["chunker"] == "section"
    assert stats["count"] >= 3
    assert stats["mean_tokens"] > 0


def test_cli_chunk_help() -> None:
    result = runner.invoke(app, ["chunk", "--help"], color=False)
    assert result.exit_code == 0
    assert "--chunker" in result.stdout
    assert "--size" in result.stdout
    assert "--overlap" in result.stdout


@pytest.mark.parametrize("chunker", (ChunkerName.FIXED, ChunkerName.RECURSIVE, ChunkerName.SECTION))
@pytest.mark.parametrize("label", ("fixture", "long", "table"))
def test_chunk_spans_are_verbatim(chunker: ChunkerName, label: str) -> None:
    document = {"fixture": _parsed, "long": _long_parsed, "table": _table_parsed}[label]()
    chunks, _ = chunk_document(document, chunker=chunker, size=512, overlap=64)
    assert chunks
    for chunk in chunks:
        _assert_verbatim(document, chunk)


@pytest.mark.parametrize("chunker", (ChunkerName.FIXED, ChunkerName.RECURSIVE, ChunkerName.SECTION))
def test_oversized_item7_table_stays_valid_markdown(chunker: ChunkerName) -> None:
    document = _table_parsed()
    assert "Capital metric 80" in document.text
    chunks, _ = chunk_document(document, chunker=chunker, size=512, overlap=64)
    table_chunks = [chunk for chunk in chunks if _markdown_tables(chunk.text)]
    assert table_chunks
    if chunker == ChunkerName.SECTION:
        item7 = [chunk for chunk in chunks if chunk.section == "Item 7"]
        assert len(item7) >= 2
        assert any(_markdown_tables(chunk.text) for chunk in item7)
        assert any(chunk.header_prefix for chunk in item7)
        assert len({chunk.start for chunk in item7}) == len(item7)
        assert all(chunk.token_count <= 512 for chunk in item7)
    for chunk in table_chunks:
        tables = _markdown_tables(chunk.text)
        assert tables
        for table in tables:
            _assert_valid_table(table)
            assert "Capital metric" in table


def test_long_table_fixture_detects_part_heading_and_skips_xref() -> None:
    document = _table_parsed()
    names = [span.name for span in document.sections]
    assert names == ["body", "Item 1", "Item 7", "Item 8"]
    item1 = next(span for span in document.sections if span.name == "Item 1")
    item7 = next(span for span in document.sections if span.name == "Item 7")
    assert "Item 7. See page 45" in document.text[item1.start : item1.end]
    assert document.text[item7.start : item7.end].startswith("Part II — Item 7")
    assert "Capital metric 01" in document.text[item7.start : item7.end]


def test_pdf_records_page_and_offset() -> None:
    pdf = _pdf_with_text("Item 7. Management Discussion\nCET1 was 15.1 percent.")
    document = pdf_to_document(pdf, SOURCE)
    assert document.media_type == "pdf"
    assert document.pages
    assert document.pages[0].name == "1"
    chunks, _ = chunk_document(document, chunker=ChunkerName.SECTION, size=512, overlap=64)
    assert chunks
    assert chunks[0].page == 1
    assert chunks[0].offset >= 0
    assert "Item 7" in {chunk.section for chunk in chunks}


def _pdf_with_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1", "replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        (
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        ),
        (
            b"4 0 obj << /Length "
            + str(len(content)).encode()
            + b" >> stream\n"
            + content
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    body = b"%PDF-1.4\n" + b"".join(objects)
    xref_positions = []
    cursor = len(b"%PDF-1.4\n")
    for obj in objects:
        xref_positions.append(cursor)
        cursor += len(obj)
    xref = [b"xref\n0 6\n0000000000 65535 f \n"]
    for pos in xref_positions:
        xref.append(f"{pos:010d} 00000 n \n".encode())
    trailer = (
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n" + str(cursor).encode() + b"\n%%EOF\n"
    )
    return body + b"".join(xref) + trailer
