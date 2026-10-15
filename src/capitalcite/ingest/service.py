"""Orchestrate ticker lookup, listing, download, and idempotent store."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from capitalcite.ingest.client import EdgarAccessError, EdgarClient, EdgarUnavailableError
from capitalcite.ingest.models import Filing, FilingMetadata
from capitalcite.ingest.store import FilingStore

logger = logging.getLogger(__name__)


@dataclass
class IngestSummary:
    """Counts and stored metadata from one ingest run."""

    downloaded: list[FilingMetadata] = field(default_factory=list)
    skipped: list[Filing] = field(default_factory=list)
    failed: list[tuple[Filing, str]] = field(default_factory=list)

    @property
    def downloaded_count(self) -> int:
        return len(self.downloaded)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def failed_count(self) -> int:
        return len(self.failed)

    @property
    def filing_count(self) -> int:
        return self.downloaded_count + self.skipped_count + self.failed_count


async def run_ingest(
    *,
    client: EdgarClient,
    store: FilingStore,
    tickers: Sequence[str],
    forms: Sequence[str],
    years: frozenset[int],
) -> IngestSummary:
    """Download matching primary documents. Existing files are left in place."""
    companies = await client.lookup_companies(tickers)
    summary = IngestSummary()
    for ticker in tickers:
        company = companies[ticker]
        filings = await client.list_filings(company, forms=forms, years=years)
        logger.info(
            "ingest %s CIK %s: %s matching filing(s)",
            company.ticker,
            company.cik,
            len(filings),
        )
        for filing in filings:
            try:
                filing = await client.resolve_primary(filing)
            except (EdgarAccessError, EdgarUnavailableError):
                raise
            except Exception as exc:
                logger.exception(
                    "failed to resolve primary %s %s %s %s",
                    filing.ticker,
                    filing.form,
                    filing.period,
                    filing.accession,
                )
                summary.failed.append((filing, str(exc)))
                continue
            if store.exists(filing):
                logger.info("skip existing %s %s %s", filing.ticker, filing.form, filing.period)
                summary.skipped.append(filing)
                continue
            try:
                body = await client.download_primary(filing)
                metadata = store.write(filing, body)
            except (EdgarAccessError, EdgarUnavailableError):
                raise
            except Exception as exc:
                logger.exception(
                    "failed %s %s %s %s",
                    filing.ticker,
                    filing.form,
                    filing.period,
                    filing.accession,
                )
                summary.failed.append((filing, str(exc)))
                continue
            logger.info(
                "wrote %s (%s bytes) %s",
                store.document_path(filing),
                metadata.bytes,
                metadata.url,
            )
            summary.downloaded.append(metadata)
    return summary
