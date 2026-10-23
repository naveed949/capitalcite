"""Holding companies whose lead bank we can name. Certificates are not stored here."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator


class HoldingCompany(BaseModel):
    """One holding company. The lead bank's certificate is resolved later."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ticker: str
    cik: str
    lead_bank_name: str

    @model_validator(mode="after")
    def well_formed(self) -> HoldingCompany:
        ticker_ok = self.ticker.isascii() and self.ticker.isalnum()
        if not ticker_ok or self.ticker != self.ticker.upper():
            raise ValueError("ticker must be uppercase ASCII letters and digits")
        if len(self.cik) != 10 or not self.cik.isdigit():
            raise ValueError("cik must be 10 digits")
        if not self.lead_bank_name.strip() or '"' in self.lead_bank_name:
            raise ValueError("lead bank name must be non-empty and contain no double quote")
        return self


_COMPANIES: tuple[HoldingCompany, ...] = (
    HoldingCompany(
        ticker="JPM",
        cik="0000019617",
        lead_bank_name="JPMORGAN CHASE BANK, NATIONAL ASSOCIATION",
    ),
    HoldingCompany(
        ticker="BAC",
        cik="0000070858",
        lead_bank_name="BANK OF AMERICA, NATIONAL ASSOCIATION",
    ),
    HoldingCompany(
        ticker="WFC",
        cik="0000072971",
        lead_bank_name="WELLS FARGO BANK, NATIONAL ASSOCIATION",
    ),
    HoldingCompany(
        ticker="C",
        cik="0000831001",
        lead_bank_name="CITIBANK, N.A.",
    ),
)


def holding_companies() -> tuple[HoldingCompany, ...]:
    return _COMPANIES


def holding_company(ticker: str) -> HoldingCompany:
    """Uppercase the ticker. Raise KeyError when it is not in the table."""
    symbol = ticker.strip().upper()
    for company in _COMPANIES:
        if company.ticker == symbol:
            return company
    raise KeyError(symbol)
