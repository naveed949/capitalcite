"""Filing records written next to downloaded primary documents."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Company(BaseModel):
    """A ticker resolved through company_tickers.json."""

    ticker: str
    cik: str = Field(description="10-digit CIK, zero-padded.")
    title: str

    @property
    def cik_int(self) -> int:
        return int(self.cik)


class Filing(BaseModel):
    """One EDGAR submission selected for download."""

    ticker: str
    cik: str
    form: str
    period: str = Field(description="reportDate (YYYY-MM-DD), falling back to filingDate.")
    filing_date: str
    accession: str
    primary_document: str
    url: str

    @property
    def period_year(self) -> int:
        return int(self.period[:4])

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")


class FilingMetadata(Filing):
    """Sidecar written after a successful store."""

    sha256: str
    bytes: int
