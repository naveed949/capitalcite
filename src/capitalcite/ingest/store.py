"""Filesystem store for raw EDGAR primary documents and sidecar metadata."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from capitalcite.atomic import atomic_write_bytes, atomic_write_text
from capitalcite.ingest.models import Filing, FilingMetadata

_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TICKER_RE = re.compile(r"^[A-Z0-9]+$")


class FilingStore:
    """Write ``data/raw/edgar/{ticker}/{form}/{period}/{accession}/`` trees."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def filing_dir(self, filing: Filing) -> Path:
        ticker = _safe_ticker(filing.ticker)
        form = _safe_form(filing.form)
        period = _safe_period(filing.period)
        accession = _safe_accession(filing.accession)
        return self.root / "raw" / "edgar" / ticker / form / period / accession

    def document_path(self, filing: Filing) -> Path:
        return self.filing_dir(filing) / _safe_document(filing.primary_document)

    def metadata_path(self, filing: Filing) -> Path:
        return self.filing_dir(filing) / "metadata.json"

    def exists(self, filing: Filing) -> bool:
        return self.document_path(filing).is_file() and self.metadata_path(filing).is_file()

    def write(self, filing: Filing, body: bytes) -> FilingMetadata:
        directory = self.filing_dir(filing)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self.document_path(filing), body)
        metadata = FilingMetadata(
            **filing.model_dump(),
            sha256=hashlib.sha256(body).hexdigest(),
            bytes=len(body),
        )
        atomic_write_text(
            self.metadata_path(filing),
            json.dumps(metadata.model_dump(), indent=2, sort_keys=True) + "\n",
        )
        return metadata

    def load_metadata(self, filing: Filing) -> FilingMetadata:
        return FilingMetadata.model_validate_json(
            self.metadata_path(filing).read_text(encoding="utf-8")
        )

    def parsed_dir(self, filing: Filing) -> Path:
        ticker = _safe_ticker(filing.ticker)
        form = _safe_form(filing.form)
        period = _safe_period(filing.period)
        accession = _safe_accession(filing.accession)
        return self.root / "parsed" / "edgar" / ticker / form / period / accession

    def document_md_path(self, filing: Filing) -> Path:
        return self.parsed_dir(filing) / "document.md"

    def chunks_path(self, filing: Filing) -> Path:
        return self.parsed_dir(filing) / "chunks.jsonl"

    def chunk_stats_path(self, filing: Filing) -> Path:
        return self.parsed_dir(filing) / "stats.json"


def _safe_ticker(ticker: str) -> str:
    upper = ticker.upper()
    if not _TICKER_RE.match(upper):
        raise ValueError(f"unsafe ticker {ticker!r}")
    return upper


def _safe_form(form: str) -> str:
    cleaned = form.replace("/", "-")
    if not cleaned or cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ValueError(f"unsafe form {form!r}")
    return cleaned


def _safe_period(period: str) -> str:
    if not _PERIOD_RE.match(period):
        raise ValueError(f"unsafe period {period!r}")
    return period


def _safe_accession(accession: str) -> str:
    if not _ACCESSION_RE.match(accession):
        raise ValueError(f"unsafe accession {accession!r}")
    return accession


def _safe_document(name: str) -> str:
    base = Path(name).name
    if not base or base in {".", ".."} or base == "metadata.json":
        raise ValueError(f"unsafe primary document {name!r}")
    return base
