"""Ground handwritten evidence in parsed document.md from a local corpus."""

from __future__ import annotations

from pathlib import Path

from capitalcite.chunking.service import iter_raw_filings
from capitalcite.golden.models import Evidence, GoldenItem, ItemSource
from capitalcite.golden.store import ValidationError
from capitalcite.ingest.models import FilingMetadata
from capitalcite.ingest.store import FilingStore
from capitalcite.parse import CitationSource, parse_path
from capitalcite.parse.sections import detect_sections, section_at


class AgainstRawReport:
    def __init__(self) -> None:
        self.aligned = 0
        self.skipped_benchmark = 0
        self.errors: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors


def index_corpus(data_dir: Path) -> dict[str, tuple[FilingMetadata, Path]]:
    found: dict[str, tuple[FilingMetadata, Path]] = {}
    for metadata, path in iter_raw_filings(data_dir):
        found[metadata.accession] = (metadata, path)
    return found


def load_document_text(data_dir: Path, metadata: FilingMetadata, raw_path: Path) -> str:
    store = FilingStore(data_dir)
    md_path = store.document_md_path(metadata)
    if md_path.is_file():
        return md_path.read_text(encoding="utf-8")
    source = CitationSource(
        doc_id=metadata.accession,
        source_url=metadata.url,
        ticker=metadata.ticker,
        form=metadata.form,
        period=metadata.period,
        accession=metadata.accession,
    )
    return parse_path(raw_path, source).text


def align_evidence(item: GoldenItem, row: Evidence, text: str) -> None:
    start = text.find(row.quote)
    if start < 0:
        raise ValidationError(f"{item.id}: quote not in parsed document {row.doc_id}")
    end = start + len(row.quote)
    if row.start is not None and (row.start != start or row.end != end):
        raise ValidationError(
            f"{item.id}: stored span {row.start}:{row.end} != parsed {start}:{end} ({row.doc_id})"
        )
    row.start = start
    row.end = end
    detected = section_at(detect_sections(text), start)
    if row.section != detected:
        raise ValidationError(
            f"{item.id}: section {row.section!r} != detector {detected!r} at {start} ({row.doc_id})"
        )


def validate_against_raw(items: list[GoldenItem], data_dir: Path) -> AgainstRawReport:
    root = Path(data_dir)
    if not (root / "raw" / "edgar").is_dir():
        raise ValidationError(f"missing ingest tree: {root / 'raw' / 'edgar'}")
    corpus = index_corpus(root)
    report = AgainstRawReport()
    for item in items:
        if item.source is not ItemSource.HANDWRITTEN:
            report.skipped_benchmark += len(item.evidence)
            continue
        for row in item.evidence:
            located = corpus.get(row.doc_id)
            if located is None:
                report.errors.append(f"{item.id}: accession {row.doc_id} not in {root}")
                continue
            metadata, raw_path = located
            try:
                text = load_document_text(root, metadata, raw_path)
                align_evidence(item, row, text)
            except ValidationError as exc:
                report.errors.append(str(exc))
                continue
            report.aligned += 1
    return report
