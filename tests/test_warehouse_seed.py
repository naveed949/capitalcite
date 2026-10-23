from __future__ import annotations

import csv
import json
from pathlib import Path

from capitalcite.holdings import holding_companies

SEED = Path("dbt/seeds/int_bank_entity_map.csv")
LEADS = Path("tests/fixtures/metrics/lead_banks.json")
JPM_FACTS = Path("tests/fixtures/metrics/xbrl/JPM.json")


def test_warehouse_seed_matches_holdings() -> None:
    with SEED.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    companies = holding_companies()
    assert [row["ticker"] for row in rows] == [company.ticker for company in companies]
    by_ticker = {company.ticker: company for company in companies}
    for row in rows:
        company = by_ticker[row["ticker"]]
        assert row["cik"] == company.cik
        assert row["lead_bank_name"] == company.lead_bank_name
        assert row["source_note"]


def test_warehouse_seed_certs_match_fixture_leads() -> None:
    with SEED.open(encoding="utf-8", newline="") as handle:
        seed_certs = {row["ticker"]: row["cert"] for row in csv.DictReader(handle)}
    leads = {
        str(row["ticker"]): str(row["cert"])
        for row in json.loads(LEADS.read_text(encoding="utf-8"))
    }
    assert seed_certs == leads


def test_jpm_fixture_assets_q2_2026() -> None:
    body = json.loads(JPM_FACTS.read_text(encoding="utf-8"))
    observations = body["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    q2 = next(row for row in observations if row["end"] == "2026-06-30")
    q1 = next(row for row in observations if row["end"] == "2026-03-31" and row.get("fp") == "Q1")
    assert q2["val"] == 5015069000000
    assert q2["accn"] == "0001628280-26-054343"
    assert q1["val"] == 4900475000000


def test_jpm_fixture_has_duration_and_restated_deposits() -> None:
    body = json.loads(JPM_FACTS.read_text(encoding="utf-8"))
    deposits = body["facts"]["us-gaap"]["Deposits"]["units"]["USD"]
    income = body["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    deposit_vals = {row["filed"]: row["val"] for row in deposits if row["end"] == "2026-06-30"}
    assert deposit_vals["2026-08-01"] == 2400000000000
    assert deposit_vals["2026-08-06"] == 2500000000000
    starts = {row["start"]: row["val"] for row in income if row["end"] == "2026-06-30"}
    assert starts["2026-04-01"] == 13000000000
    assert starts["2026-01-01"] == 27000000000
    assert {row["accn"] for row in income} == {"0001628280-26-054343"}
    assets = body["facts"]["us-gaap"]["Assets"]["units"]["USD"]
    assert any("fp" not in row and row["form"] == "8-K" for row in assets)
