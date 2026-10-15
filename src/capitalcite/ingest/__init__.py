"""EDGAR ingest: CIK lookup, submissions listing, primary-document store."""

from capitalcite.ingest.service import IngestSummary, run_ingest

__all__ = ["IngestSummary", "run_ingest"]
