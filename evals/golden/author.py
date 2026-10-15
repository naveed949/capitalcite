"""Historical excerpt-based generator. Do not re-run over committed v1.

v1.jsonl was rebuilt from parsed document.md after ingest started
resolving Wells Fargo cover-page primaries to the real 10-K HTML.
CI does not run this file. The committed JSONL and passages are the
source of truth.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from capitalcite.golden.models import (
    Evidence,
    EvidenceRole,
    GoldenItem,
    ItemSource,
    QuestionType,
)
from capitalcite.golden.store import PASSAGES_DIRNAME, passage_filename

ROOT = Path("evals/golden")
EXCERPTS = Path("/tmp/capitalcite-corpus/excerpts")
FB_PATH = Path("/tmp/capitalcite-corpus/bench/financebench.jsonl")
FQ_PATH = Path("/tmp/capitalcite-corpus/bench/finqa_banks.json")

FILINGS = {
    "JPM_2023": {
        "doc_id": "0000019617-24-000225",
        "ticker": "JPM",
        "period": "2023-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/19617/000001961724000225/jpm-20231231.htm"
        ),
        "excerpt": "JPM_2023-12-31_0000019617-24-000225.txt",
    },
    "JPM_2024": {
        "doc_id": "0000019617-25-000270",
        "ticker": "JPM",
        "period": "2024-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/19617/000001961725000270/jpm-20241231.htm"
        ),
        "excerpt": "JPM_2024-12-31_0000019617-25-000270.txt",
    },
    "JPM_2025": {
        "doc_id": "0001628280-26-008131",
        "ticker": "JPM",
        "period": "2025-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/19617/000162828026008131/jpm-20251231.htm"
        ),
        "excerpt": "JPM_2025-12-31_0001628280-26-008131.txt",
    },
    "BAC_2023": {
        "doc_id": "0000070858-24-000122",
        "ticker": "BAC",
        "period": "2023-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/70858/000007085824000122/bac-20231231.htm"
        ),
        "excerpt": "BAC_2023-12-31_0000070858-24-000122.txt",
    },
    "BAC_2024": {
        "doc_id": "0000070858-25-000139",
        "ticker": "BAC",
        "period": "2024-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/70858/000007085825000139/bac-20241231.htm"
        ),
        "excerpt": "BAC_2024-12-31_0000070858-25-000139.txt",
    },
    "BAC_2025": {
        "doc_id": "0000070858-26-000157",
        "ticker": "BAC",
        "period": "2025-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/70858/000007085826000157/bac-20251231.htm"
        ),
        "excerpt": "BAC_2025-12-31_0000070858-26-000157.txt",
    },
    "C_2023": {
        "doc_id": "0000831001-24-000033",
        "ticker": "C",
        "period": "2023-12-31",
        "url": ("https://www.sec.gov/Archives/edgar/data/831001/000083100124000033/c-20231231.htm"),
        "excerpt": "C_2023-12-31_0000831001-24-000033.txt",
    },
    "C_2024": {
        "doc_id": "0000831001-25-000067",
        "ticker": "C",
        "period": "2024-12-31",
        "url": ("https://www.sec.gov/Archives/edgar/data/831001/000083100125000067/c-20241231.htm"),
        "excerpt": "C_2024-12-31_0000831001-25-000067.txt",
    },
    "C_2025": {
        "doc_id": "0000831001-26-000011",
        "ticker": "C",
        "period": "2025-12-31",
        "url": ("https://www.sec.gov/Archives/edgar/data/831001/000083100126000011/c-20251231.htm"),
        "excerpt": "C_2025-12-31_0000831001-26-000011.txt",
    },
    "WFC_2023": {
        "doc_id": "0000072971-24-000064",
        "ticker": "WFC",
        "period": "2023-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/72971/000007297124000064/wfc-20231231.htm"
        ),
        "excerpt": "WFC_2023-12-31_0000072971-24-000064.txt",
    },
    "WFC_2024": {
        "doc_id": "0000072971-25-000066",
        "ticker": "WFC",
        "period": "2024-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/72971/000007297125000066/wfc-20241231.htm"
        ),
        "excerpt": "WFC_2024-12-31_0000072971-25-000066.txt",
    },
    "WFC_2025": {
        "doc_id": "0000072971-26-000133",
        "ticker": "WFC",
        "period": "2025-12-31",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/72971/000007297126000133/wfc-20251231_d2.htm"
        ),
        "excerpt": "WFC_2025-12-31_0000072971-26-000133.txt",
    },
}


def load_excerpt(key: str) -> str:
    return (EXCERPTS / str(FILINGS[key]["excerpt"])).read_text(encoding="utf-8")


def span(text: str, needle: str, before: int = 40, after: int = 140) -> str:
    idx = text.find(needle)
    if idx < 0:
        raise SystemExit(f"needle not found: {needle!r}")
    start = max(0, idx - before)
    end = min(len(text), idx + len(needle) + after)
    return text[start:end]


def ev(key: str, section: str, quote: str, role: EvidenceRole = EvidenceRole.ANSWER) -> Evidence:
    return Evidence(doc_id=str(FILINGS[key]["doc_id"]), section=section, quote=quote, role=role)


def item(
    item_id: str,
    question: str,
    answer: str,
    evidence: list[Evidence],
    qtype: QuestionType,
    *,
    answerable: bool = True,
    source: ItemSource = ItemSource.HANDWRITTEN,
) -> GoldenItem:
    return GoldenItem(
        id=item_id,
        question=question,
        answer=answer,
        evidence=evidence,
        type=qtype,
        answerable=answerable,
        source=source,
    )


def handwritten(passages: dict[tuple[str, str], list[str]]) -> list[GoldenItem]:
    j23 = load_excerpt("JPM_2023")
    j24 = load_excerpt("JPM_2024")
    j25 = load_excerpt("JPM_2025")
    b23 = load_excerpt("BAC_2023")
    b24 = load_excerpt("BAC_2024")
    b25 = load_excerpt("BAC_2025")
    c23 = load_excerpt("C_2023")
    c24 = load_excerpt("C_2024")
    c25 = load_excerpt("C_2025")
    w23 = load_excerpt("WFC_2023")
    w25 = load_excerpt("WFC_2025")

    def add(key: str, section: str, quote: str) -> str:
        passages[(str(FILINGS[key]["doc_id"]), section)].append(quote)
        return quote

    q: dict[str, str] = {}
    q["j23_cet1"] = add(
        "JPM_2023",
        "Item 7",
        span(
            j23,
            "CET1 capital was $251 billion, and the Standardized and Advanced CET1 ratios were both 15.0%.",
        ),
    )
    q["j23_slr"] = add("JPM_2023", "Item 7", span(j23, "SLR was 6.1%."))
    q["j23_tbvps"] = add("JPM_2023", "Item 7", span(j23, "TBVPS grew 18%, ending 2023 at $86.08."))
    q["j23_hqla"] = add(
        "JPM_2023",
        "Item 7",
        span(j23, "High Quality Liquid Assets (“HQLA”) of approximately $798 billion"),
    )
    q["j23_ni"] = add("JPM_2023", "Item 8", span(j23, "Net income | | $ | 49,552 | | | $ | 37,676"))
    q["j23_eps"] = add("JPM_2023", "Item 8", span(j23, "Diluted | | 16.23 | | | 12.09"))
    q["j23_pcl"] = add(
        "JPM_2023", "Item 7", span(j23, "The provision for credit losses was $9.3 billion")
    )
    q["j23_bvps"] = add("JPM_2023", "Item 8", span(j23, "Book value per share | | 104.45"))
    q["j23_req"] = add(
        "JPM_2023",
        "Item 7",
        span(
            j23,
            "Standardized CET1 capital ratio requirement, including regulatory buffers, was 11.4%",
        ),
    )
    q["j23_scb"] = add(
        "JPM_2023",
        "Item 7",
        span(j23, "The Firm's current SCB requirement is 2.9%"),
    )
    q["j23_rotce"] = add(
        "JPM_2023",
        "Item 7",
        span(j23, "Return on tangible common equity (“ROTCE”) (a)"),
    )

    q["j24_cet1"] = add(
        "JPM_2024",
        "Item 7",
        span(
            j24,
            "CET1 capital was $275.5 billion, and the Standardized and Advanced CET1 ratios were 15.7% and 15.8%",
        ),
    )
    q["j24_slr"] = add("JPM_2024", "Item 7", span(j24, "SLR was 6.1%."))
    q["j24_tbvps"] = add(
        "JPM_2024", "Item 7", span(j24, "TBVPS grew 13.0%, ending 2024 at $97.30.")
    )
    q["j24_hqla"] = add(
        "JPM_2024",
        "Item 7",
        span(j24, "High Quality Liquid Assets (“HQLA”) of approximately $834 billion"),
    )
    q["j24_ni"] = add("JPM_2024", "Item 8", span(j24, "Net income | | $ | 58,471 | | | $ | 49,552"))
    q["j24_eps"] = add("JPM_2024", "Item 8", span(j24, "Diluted | | 19.75 | | | 16.23"))
    q["j24_pcl"] = add(
        "JPM_2024", "Item 7", span(j24, "The provision for credit losses was $10.7 billion")
    )
    q["j24_div"] = add(
        "JPM_2024", "Item 8", span(j24, "Cash dividends declared per share | | 4.80")
    )
    q["j24_req"] = add(
        "JPM_2024",
        "Item 7",
        span(
            j24,
            "Standardized CET1 capital ratio requirement, including regulatory buffers, was 12.3%",
        ),
    )
    q["j24_scb"] = add(
        "JPM_2024",
        "Item 7",
        span(j24, "The Firm's current SCB requirement is 3.3%"),
    )

    q["j25_cet1"] = add(
        "JPM_2025",
        "Item 7",
        span(
            j25,
            "CET1 capital was $288.5 billion, and the Standardized and Advanced CET1 ratios were 14.6% and 14.1%",
        ),
    )
    q["j25_slr"] = add("JPM_2025", "Item 7", span(j25, "SLR was 5.8%."))
    q["j25_tbvps"] = add(
        "JPM_2025", "Item 7", span(j25, "TBVPS grew 10.5%, ending 2025 at $107.56.")
    )
    q["j25_ni"] = add("JPM_2025", "Item 8", span(j25, "Net income | | $ | 57,048 | | | $ | 58,471"))
    q["j25_eps"] = add("JPM_2025", "Item 8", span(j25, "Diluted | | 20.02 | | | 19.75"))
    q["j25_pcl"] = add(
        "JPM_2025", "Item 7", span(j25, "The provision for credit losses was $14.2 billion")
    )
    q["j25_div"] = add(
        "JPM_2025", "Item 8", span(j25, "Cash dividends declared per share | | 5.80")
    )
    q["j25_req"] = add(
        "JPM_2025",
        "Item 7",
        span(
            j25,
            "Standardized CET1 capital ratio requirement, including regulatory buffers, was 11.5%",
        ),
    )
    q["j25_dep"] = add(
        "JPM_2025",
        "Item 7",
        span(j25, "Firmwide average deposits of $2.5 trillion were up 5%"),
    )
    q["j25_scb"] = add(
        "JPM_2025",
        "Item 7",
        span(j25, "The Firm's current SCB requirement is 2.5%"),
    )

    q["b23_cet1"] = add(
        "BAC_2023",
        "Item 7",
        span(b23, "CET1 capital ratio of 11.8 percent under the Standardized approach exceeded"),
    )
    q["b23_ni"] = add(
        "BAC_2023",
        "Item 7",
        span(b23, "Net income was $26.5 billion, or $3.08 per diluted share in 2023"),
    )
    q["b24_cet1"] = add(
        "BAC_2024",
        "Item 7",
        span(b24, "CET1 capital ratio of 11.9 percent under the Standardized approach exceeded"),
    )
    q["b24_ni"] = add(
        "BAC_2024",
        "Item 7",
        span(b24, "Net income was $27.1 billion, or $3.21 per diluted share in 2024"),
    )
    q["b25_cet1"] = add(
        "BAC_2025",
        "Item 7",
        span(b25, "At December\xa031, 2025, the Corporation’s CET1 capital ratio was 11.4 percent"),
    )
    q["b25_ni"] = add(
        "BAC_2025",
        "Item 7",
        span(b25, "Net income was $30.5 billion, or $3.81 per diluted share, in 2025"),
    )
    q["b25_min"] = add(
        "BAC_2025",
        "Item 7",
        span(b25, "minimum CET1 capital ratio requirement of 10.0 percent"),
    )

    q["c23_cet1"] = add(
        "C_2023",
        "Item 7",
        span(
            c23,
            "Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach increased to 13.4%",
        ),
    )
    q["c23_ni"] = add(
        "C_2023",
        "Item 7",
        span(c23, "Citigroup reported net income of $9.2 billion, or $4.04 per share"),
    )
    q["c24_cet1"] = add(
        "C_2024",
        "Item 7",
        span(
            c24,
            "Common Equity Tier 1 (CET1) Capital ratio under the Basel III Standardized Approach was 13.6%",
        ),
    )
    q["c24_req"] = add(
        "C_2024",
        "Item 7",
        span(c24, "required regulatory CET1 Capital ratio of 12.1%"),
    )
    q["c24_ni"] = add(
        "C_2024", "Item 7", span(c24, "Net income increased 37% versus the prior year")
    )
    q["c25_cet1"] = add(
        "C_2025",
        "Item 7",
        span(
            c25,
            "Citigroup’s Common Equity Tier 1 (CET1) Capital ratio was 13.2% as of December 31, 2025",
        ),
    )
    q["c25_dep"] = add(
        "C_2025",
        "Item 7",
        span(c25, "Citigroup’s average deposits in 2025 were approximately $1.4 trillion"),
    )
    q["c25_bvps"] = add("C_2025", "Item 8", span(c25, "Book value per common share | $ | 110.01"))

    q["w23_name"] = add("WFC_2023", "body", span(w23, "WELLS FARGO & COMPANY/MN"))
    q["w25_name"] = add("WFC_2025", "body", span(w25, "WELLS FARGO & COMPANY"))
    q["w25_addr"] = add("WFC_2025", "body", span(w25, "333 Market Street"))
    q["w25_state"] = add(
        "WFC_2025",
        "body",
        span(w25, "(State or other jurisdiction of incorporation or organization)"),
    )
    q["w25_form"] = add(
        "WFC_2025",
        "body",
        span(
            w25,
            "Annual Report Pursuant to Section 13 or 15(d) of the Securities Exchange Act of 1934",
        ),
    )

    items: list[GoldenItem] = [
        item(
            "cc-h-001",
            "What was JPMorgan's Standardized CET1 capital ratio at year-end 2023?",
            "15.0%",
            [ev("JPM_2023", "Item 7", q["j23_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-002",
            "How much CET1 capital did JPMorgan report at year-end 2023?",
            "$251 billion",
            [ev("JPM_2023", "Item 7", q["j23_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-003",
            "What was JPMorgan's supplementary leverage ratio (SLR) at year-end 2023?",
            "6.1%",
            [ev("JPM_2023", "Item 7", q["j23_slr"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-004",
            "What was JPMorgan's tangible book value per share at year-end 2023?",
            "$86.08",
            [ev("JPM_2023", "Item 7", q["j23_tbvps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-005",
            "Approximately how much eligible end-of-period HQLA did JPMorgan report as of December 31, 2023?",
            "approximately $798 billion",
            [ev("JPM_2023", "Item 7", q["j23_hqla"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-006",
            "What was JPMorgan's 2023 net income?",
            "$49.552 billion",
            [ev("JPM_2023", "Item 8", q["j23_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-007",
            "What was JPMorgan's 2023 diluted earnings per share?",
            "$16.23",
            [ev("JPM_2023", "Item 8", q["j23_eps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-008",
            "What provision for credit losses did JPMorgan record in 2023?",
            "$9.3 billion",
            [ev("JPM_2023", "Item 7", q["j23_pcl"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-009",
            "What was JPMorgan's book value per share at year-end 2023?",
            "$104.45",
            [ev("JPM_2023", "Item 8", q["j23_bvps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-010",
            "What was JPMorgan's Standardized CET1 capital ratio requirement, including buffers, as of December 31, 2023?",
            "11.4%",
            [ev("JPM_2023", "Item 7", q["j23_req"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-011",
            "What SCB requirement was in effect for JPMorgan as of the 2023 10-K?",
            "2.9%",
            [ev("JPM_2023", "Item 7", q["j23_scb"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-012",
            "What return on tangible common equity did JPMorgan report for 2023?",
            "21%",
            [ev("JPM_2023", "Item 7", q["j23_rotce"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-013",
            "What was JPMorgan's Standardized CET1 capital ratio at year-end 2024?",
            "15.7%",
            [ev("JPM_2024", "Item 7", q["j24_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-014",
            "What was JPMorgan's Advanced CET1 capital ratio at year-end 2024?",
            "15.8%",
            [ev("JPM_2024", "Item 7", q["j24_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-015",
            "How much CET1 capital did JPMorgan report at year-end 2024?",
            "$275.5 billion",
            [ev("JPM_2024", "Item 7", q["j24_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-016",
            "What was JPMorgan's SLR at year-end 2024?",
            "6.1%",
            [ev("JPM_2024", "Item 7", q["j24_slr"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-017",
            "What was JPMorgan's TBVPS at year-end 2024?",
            "$97.30",
            [ev("JPM_2024", "Item 7", q["j24_tbvps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-018",
            "Approximately how much eligible end-of-period HQLA did JPMorgan report as of December 31, 2024?",
            "approximately $834 billion",
            [ev("JPM_2024", "Item 7", q["j24_hqla"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-019",
            "What was JPMorgan's 2024 net income?",
            "$58.471 billion",
            [ev("JPM_2024", "Item 8", q["j24_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-020",
            "What was JPMorgan's 2024 diluted EPS?",
            "$19.75",
            [ev("JPM_2024", "Item 8", q["j24_eps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-021",
            "What provision for credit losses did JPMorgan record in 2024?",
            "$10.7 billion",
            [ev("JPM_2024", "Item 7", q["j24_pcl"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-022",
            "What cash dividend per share did JPMorgan declare in 2024?",
            "$4.80",
            [ev("JPM_2024", "Item 8", q["j24_div"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-023",
            "What was JPMorgan's Standardized CET1 requirement, including buffers, as of December 31, 2024?",
            "12.3%",
            [ev("JPM_2024", "Item 7", q["j24_req"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-024",
            "What SCB requirement was in effect for JPMorgan in the 2024 10-K?",
            "3.3%",
            [ev("JPM_2024", "Item 7", q["j24_scb"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-025",
            "What was JPMorgan's Standardized CET1 capital ratio at year-end 2025?",
            "14.6%",
            [ev("JPM_2025", "Item 7", q["j25_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-026",
            "What was JPMorgan's Advanced CET1 capital ratio at year-end 2025?",
            "14.1%",
            [ev("JPM_2025", "Item 7", q["j25_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-027",
            "How much CET1 capital did JPMorgan report at year-end 2025?",
            "$288.5 billion",
            [ev("JPM_2025", "Item 7", q["j25_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-028",
            "What was JPMorgan's SLR at year-end 2025?",
            "5.8%",
            [ev("JPM_2025", "Item 7", q["j25_slr"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-029",
            "What was JPMorgan's TBVPS at year-end 2025?",
            "$107.56",
            [ev("JPM_2025", "Item 7", q["j25_tbvps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-030",
            "What was JPMorgan's 2025 net income?",
            "$57.048 billion",
            [ev("JPM_2025", "Item 8", q["j25_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-031",
            "What was JPMorgan's 2025 diluted EPS?",
            "$20.02",
            [ev("JPM_2025", "Item 8", q["j25_eps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-032",
            "What provision for credit losses did JPMorgan record in 2025?",
            "$14.2 billion",
            [ev("JPM_2025", "Item 7", q["j25_pcl"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-033",
            "What cash dividend per share did JPMorgan declare in 2025?",
            "$5.80",
            [ev("JPM_2025", "Item 8", q["j25_div"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-034",
            "What was JPMorgan's Standardized CET1 requirement, including buffers, as of December 31, 2025?",
            "11.5%",
            [ev("JPM_2025", "Item 7", q["j25_req"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-035",
            "What were JPMorgan's firmwide average deposits in 2025?",
            "$2.5 trillion",
            [ev("JPM_2025", "Item 7", q["j25_dep"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-036",
            "What SCB requirement was in effect for JPMorgan in the 2025 10-K?",
            "2.5%",
            [ev("JPM_2025", "Item 7", q["j25_scb"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-037",
            "What was Bank of America's Standardized CET1 capital ratio at December 31, 2023?",
            "11.8%",
            [ev("BAC_2023", "Item 7", q["b23_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-038",
            "What was Bank of America's 2023 net income and diluted EPS?",
            "$26.5 billion, or $3.08 per diluted share",
            [ev("BAC_2023", "Item 7", q["b23_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-039",
            "What was Bank of America's Standardized CET1 capital ratio at December 31, 2024?",
            "11.9%",
            [ev("BAC_2024", "Item 7", q["b24_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-040",
            "What was Bank of America's 2024 net income and diluted EPS?",
            "$27.1 billion, or $3.21 per diluted share",
            [ev("BAC_2024", "Item 7", q["b24_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-041",
            "What was Bank of America's Standardized CET1 capital ratio at December 31, 2025?",
            "11.4%",
            [ev("BAC_2025", "Item 7", q["b25_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-042",
            "What was Bank of America's 2025 net income and diluted EPS?",
            "$30.5 billion, or $3.81 per diluted share",
            [ev("BAC_2025", "Item 7", q["b25_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-043",
            "What minimum CET1 capital ratio was Bank of America required to maintain at December 31, 2025?",
            "10.0%",
            [ev("BAC_2025", "Item 7", q["b25_min"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-044",
            "What was Citigroup's Basel III Standardized CET1 capital ratio at December 31, 2023?",
            "13.4%",
            [ev("C_2023", "Item 7", q["c23_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-045",
            "What net income and EPS did Citigroup report for 2023?",
            "$9.2 billion, or $4.04 per share",
            [ev("C_2023", "Item 7", q["c23_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-046",
            "What was Citigroup's Basel III Standardized CET1 capital ratio at December 31, 2024?",
            "13.6%",
            [ev("C_2024", "Item 7", q["c24_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-047",
            "What required regulatory CET1 capital ratio did Citigroup cite as of December 31, 2024?",
            "12.1%",
            [ev("C_2024", "Item 7", q["c24_req"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-048",
            "By what percent did Citigroup's 2024 net income increase versus 2023?",
            "37%",
            [ev("C_2024", "Item 7", q["c24_ni"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-049",
            "What was Citigroup's CET1 capital ratio at December 31, 2025?",
            "13.2%",
            [ev("C_2025", "Item 7", q["c25_cet1"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-050",
            "What were Citigroup's average deposits in 2025?",
            "approximately $1.4 trillion",
            [ev("C_2025", "Item 7", q["c25_dep"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-051",
            "What was Citigroup's book value per common share at year-end 2025?",
            "$110.01",
            [ev("C_2025", "Item 8", q["c25_bvps"])],
            QuestionType.NUMERIC,
        ),
        item(
            "cc-h-052",
            "Did JPMorgan's Standardized CET1 ratio rise or fall from year-end 2024 to year-end 2025?",
            "It fell from 15.7% to 14.6%.",
            [
                ev("JPM_2024", "Item 7", q["j24_cet1"], EvidenceRole.ANSWER),
                ev("JPM_2025", "Item 7", q["j25_cet1"], EvidenceRole.RELEVANT),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-053",
            "How did JPMorgan's net income change from 2023 to 2024?",
            "It rose from $49.552 billion to $58.471 billion.",
            [
                ev("JPM_2023", "Item 8", q["j23_ni"], EvidenceRole.RELEVANT),
                ev("JPM_2024", "Item 8", q["j24_ni"], EvidenceRole.ANSWER),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-054",
            "How did JPMorgan's net income change from 2024 to 2025?",
            "It fell from $58.471 billion to $57.048 billion.",
            [
                ev("JPM_2024", "Item 8", q["j24_ni"], EvidenceRole.RELEVANT),
                ev("JPM_2025", "Item 8", q["j25_ni"], EvidenceRole.ANSWER),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-055",
            "Which bank had the higher Standardized CET1 ratio at year-end 2024, JPMorgan or Bank of America?",
            "JPMorgan (15.7% vs Bank of America's 11.9%).",
            [
                ev("JPM_2024", "Item 7", q["j24_cet1"], EvidenceRole.ANSWER),
                ev("BAC_2024", "Item 7", q["b24_cet1"], EvidenceRole.RELEVANT),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-056",
            "Which bank had the higher Standardized CET1 ratio at year-end 2024, JPMorgan or Citigroup?",
            "JPMorgan (15.7% vs Citigroup's 13.6%).",
            [
                ev("JPM_2024", "Item 7", q["j24_cet1"], EvidenceRole.ANSWER),
                ev("C_2024", "Item 7", q["c24_cet1"], EvidenceRole.RELEVANT),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-057",
            "How did Bank of America's Standardized CET1 ratio change from 2023 to 2024?",
            "It rose from 11.8% to 11.9%.",
            [
                ev("BAC_2023", "Item 7", q["b23_cet1"], EvidenceRole.RELEVANT),
                ev("BAC_2024", "Item 7", q["b24_cet1"], EvidenceRole.ANSWER),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-058",
            "How did Citigroup's Standardized CET1 ratio change from 2023 to 2024?",
            "It rose from 13.4% to 13.6%.",
            [
                ev("C_2023", "Item 7", q["c23_cet1"], EvidenceRole.RELEVANT),
                ev("C_2024", "Item 7", q["c24_cet1"], EvidenceRole.ANSWER),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-059",
            "How did JPMorgan's TBVPS change from year-end 2023 to year-end 2024?",
            "It rose from $86.08 to $97.30.",
            [
                ev("JPM_2023", "Item 7", q["j23_tbvps"], EvidenceRole.RELEVANT),
                ev("JPM_2024", "Item 7", q["j24_tbvps"], EvidenceRole.ANSWER),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-060",
            "Did Bank of America's 2025 net income exceed its 2024 net income?",
            "Yes. 2025 net income was $30.5 billion versus $27.1 billion in 2024.",
            [
                ev("BAC_2025", "Item 7", q["b25_ni"], EvidenceRole.ANSWER),
                ev("BAC_2024", "Item 7", q["b24_ni"], EvidenceRole.RELEVANT),
            ],
            QuestionType.COMPARISON,
        ),
        item(
            "cc-h-061",
            "Did JPMorgan's year-end 2024 Standardized CET1 ratio exceed its 12.3% requirement including buffers?",
            "Yes. The ratio was 15.7% against a 12.3% requirement.",
            [
                ev("JPM_2024", "Item 7", q["j24_cet1"], EvidenceRole.ANSWER),
                ev("JPM_2024", "Item 7", q["j24_req"], EvidenceRole.RELEVANT),
            ],
            QuestionType.MULTI_HOP,
        ),
        item(
            "cc-h-062",
            "Did Citigroup's year-end 2024 CET1 ratio exceed the 12.1% required regulatory ratio it disclosed?",
            "Yes. The ratio was 13.6% versus a 12.1% requirement.",
            [
                ev("C_2024", "Item 7", q["c24_cet1"], EvidenceRole.ANSWER),
                ev("C_2024", "Item 7", q["c24_req"], EvidenceRole.RELEVANT),
            ],
            QuestionType.MULTI_HOP,
        ),
        item(
            "cc-h-063",
            "What registrant name appears on Wells Fargo's FY2025 Form 10-K cover?",
            "WELLS FARGO & COMPANY",
            [ev("WFC_2025", "body", q["w25_name"])],
            QuestionType.EXTRACTIVE,
        ),
        item(
            "cc-h-064",
            "What street address does Wells Fargo list as its principal executive offices on the FY2025 10-K cover?",
            "333 Market Street, San Francisco, California 94105",
            [ev("WFC_2025", "body", q["w25_addr"])],
            QuestionType.EXTRACTIVE,
        ),
        item(
            "cc-h-065",
            "What form of report is the Wells Fargo FY2025 filing on the ingested primary document?",
            "Form 10-K annual report pursuant to Section 13 or 15(d)",
            [ev("WFC_2025", "body", q["w25_form"])],
            QuestionType.EXTRACTIVE,
        ),
        item(
            "cc-h-066",
            "What registrant name string appears at the start of Wells Fargo's FY2023 ingested primary document?",
            "WELLS FARGO & COMPANY/MN",
            [ev("WFC_2023", "body", q["w23_name"])],
            QuestionType.EXTRACTIVE,
        ),
        item(
            "cc-h-067",
            "What was JPMorgan's 2023 net income, and did that figure also appear as the prior-year amount in the 2024 income table?",
            "$49.552 billion, and yes, the 2024 table restates 2023 net income as $49,552 million.",
            [
                ev("JPM_2023", "Item 8", q["j23_ni"], EvidenceRole.ANSWER),
                ev("JPM_2024", "Item 8", q["j24_ni"], EvidenceRole.RELEVANT),
            ],
            QuestionType.MULTI_HOP,
        ),
        item(
            "cc-u-001",
            "What CET1 capital ratio did Wells Fargo disclose in the ingested FY2025 primary HTML document?",
            "Refuse. The ingested Wells Fargo FY2025 primary document is a Form 10-K cover and exhibit list. It does not state a CET1 ratio.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-002",
            "What was Wells Fargo's 2024 net income according to the ingested FY2024 primary HTML?",
            "Refuse. The ingested Wells Fargo FY2024 primary HTML is an iXBRL shell without an MD&A net-income narrative.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-003",
            "What was JPMorgan's year-end 2026 Standardized CET1 ratio?",
            "Refuse. The corpus has JPMorgan 10-Ks through FY2025 only. FY2026 is not filed here.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-004",
            "What CET1 ratio did Goldman Sachs report in this golden corpus?",
            "Refuse. Goldman Sachs filings were not ingested. The handwritten set covers JPM, BAC, WFC, and C.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-005",
            "How many bitcoin did JPMorgan hold on its FY2024 balance sheet?",
            "Refuse. The JPMorgan FY2024 passages used for this set do not disclose a bitcoin holding.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-006",
            "What was PNC's year-end 2024 CET1 ratio in the ingested 10-K set?",
            "Refuse. PNC is not one of the ingested tickers.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-007",
            "What was JPMorgan's Standardized CET1 ratio for the third quarter of 2025?",
            "Refuse. The handwritten items are grounded in annual 10-Ks, not a 2025 10-Q.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-008",
            "Compare JPMorgan's 2024 CET1 ratio with First National Bank of Springfield.",
            "Refuse. First National Bank of Springfield is not in the corpus.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-009",
            "What LCR did Wells Fargo report in the ingested FY2025 primary document?",
            "Refuse. The ingested Wells Fargo FY2025 primary document does not state an LCR.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-010",
            "What was Bank of America's CET1 ratio at June 30, 2025?",
            "Refuse. The ingested Bank of America documents are year-end 10-Ks, not a mid-year 2025 10-Q.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-011",
            "Which bank in this corpus reported the highest CET1 ratio in FY2027?",
            "Refuse. FY2027 filings are not in the corpus.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-u-012",
            "What personal net worth did JPMorgan's CEO report in the FY2024 10-K?",
            "Refuse. A 10-K does not report the CEO's personal net worth, and no such figure is in the passages used here.",
            [],
            QuestionType.UNANSWERABLE,
            answerable=False,
        ),
        item(
            "cc-t-001",
            "JPMorgan's year-end 2024 Standardized CET1 ratio was 9.1%. What components made up that 9.1%?",
            "Refuse. The 2024 10-K reports a Standardized CET1 ratio of 15.7%, not 9.1%.",
            [ev("JPM_2024", "Item 7", q["j24_cet1"])],
            QuestionType.TRICK,
            answerable=False,
        ),
        item(
            "cc-t-002",
            "Bank of America's year-end 2024 Standardized CET1 ratio was 15.7%. Confirm that figure.",
            "Refuse. 15.7% is JPMorgan's year-end 2024 Standardized CET1 ratio. Bank of America reported 11.9%.",
            [
                ev("BAC_2024", "Item 7", q["b24_cet1"]),
                ev("JPM_2024", "Item 7", q["j24_cet1"], EvidenceRole.RELEVANT),
            ],
            QuestionType.TRICK,
            answerable=False,
        ),
        item(
            "cc-t-003",
            "Did JPMorgan report 2024 net income of $100 billion?",
            "No. The 2024 10-K reports net income of $58.471 billion.",
            [ev("JPM_2024", "Item 8", q["j24_ni"])],
            QuestionType.TRICK,
            answerable=False,
        ),
        item(
            "cc-t-004",
            "Citigroup's 2023 net income was $49.6 billion. Is that correct?",
            "No. Citigroup reported 2023 net income of $9.2 billion. $49.552 billion is JPMorgan's 2023 net income.",
            [
                ev("C_2023", "Item 7", q["c23_ni"]),
                ev("JPM_2023", "Item 8", q["j23_ni"], EvidenceRole.RELEVANT),
            ],
            QuestionType.TRICK,
            answerable=False,
        ),
        item(
            "cc-t-005",
            "Wells Fargo's ingested FY2025 primary HTML states a CET1 ratio of 11.4%. True or false?",
            "False. 11.4% is Bank of America's year-end 2025 Standardized CET1 ratio. The Wells Fargo primary HTML does not state CET1.",
            [ev("BAC_2025", "Item 7", q["b25_cet1"])],
            QuestionType.TRICK,
            answerable=False,
        ),
        item(
            "cc-t-006",
            "JPMorgan declared a $16.23 cash dividend per share in 2023. Correct?",
            "No. $16.23 is 2023 diluted EPS. The 2023 selected-metrics table lists cash dividends declared per share next to book value, not as $16.23.",
            [ev("JPM_2023", "Item 8", q["j23_eps"])],
            QuestionType.TRICK,
            answerable=False,
        ),
    ]
    return items


def financebench(passages: dict[tuple[str, str], list[str]]) -> list[GoldenItem]:
    wanted = {
        "financebench_id_00299",
        "financebench_id_02119",
        "financebench_id_00206",
        "financebench_id_00394",
        "financebench_id_02049",
        "financebench_id_00476",
        "financebench_id_01028",
        "financebench_id_01351",
        "financebench_id_01964",
        "financebench_id_03029",
        "financebench_id_04672",
        "financebench_id_01091",
        "financebench_id_10285",
    }
    recs = []
    with FB_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            rec = json.loads(line)
            if rec.get("financebench_id") in wanted:
                recs.append(rec)
    if len(recs) != len(wanted):
        raise SystemExit(
            f"financebench missing ids: {wanted - {r['financebench_id'] for r in recs}}"
        )

    items: list[GoldenItem] = []
    for rec in recs:
        evidence_rows = rec["evidence"]
        evs: list[Evidence] = []
        for idx, row in enumerate(evidence_rows):
            doc_id = str(row.get("doc_name") or rec["doc_name"])
            page = row.get("evidence_page_num")
            section = f"page {page}" if page is not None else "body"
            quote = str(row["evidence_text"])
            if len(quote) > 900:
                quote = quote[:900]
            passages[(doc_id, section)].append(quote)
            evs.append(
                Evidence(
                    doc_id=doc_id,
                    section=section,
                    quote=quote,
                    role=EvidenceRole.ANSWER if idx == 0 else EvidenceRole.RELEVANT,
                )
            )
        qtype = QuestionType.NUMERIC
        if rec.get("question_type") == "domain-relevant" and not any(
            ch.isdigit() for ch in str(rec["answer"])
        ):
            qtype = QuestionType.EXTRACTIVE
        items.append(
            item(
                f"cc-fb-{rec['financebench_id'].removeprefix('financebench_id_')}",
                rec["question"],
                str(rec["answer"]),
                evs,
                qtype,
                source=ItemSource.FINANCEBENCH,
            )
        )
    return items


def finqa(passages: dict[tuple[str, str], list[str]]) -> list[GoldenItem]:
    rows = json.loads(FQ_PATH.read_text(encoding="utf-8"))
    picked = rows[:10]
    items: list[GoldenItem] = []
    for rec in picked:
        doc_id = str(rec["filename"])
        section = "page"
        parts = [str(p) for p in (rec.get("pre_text") or [])[:3] if p]
        table = rec.get("table")
        if isinstance(table, list):
            parts.append(json.dumps(table[:6], ensure_ascii=False))
        quote = "\n".join(parts)
        if not quote.strip():
            raise SystemExit(f"empty finqa passage {rec.get('id')}")
        if len(quote) > 1200:
            quote = quote[:1200]
        passages[(doc_id, section)].append(quote)
        qtype = QuestionType.NUMERIC
        answer = str(rec["answer"])
        if answer.lower() in {"yes", "no"}:
            qtype = QuestionType.EXTRACTIVE
        items.append(
            item(
                f"cc-fq-{str(rec['id']).replace('/', '-').replace('.pdf', '')}",
                str(rec["question"]).replace("\\n", " ").strip(),
                answer.strip(),
                [Evidence(doc_id=doc_id, section=section, quote=quote)],
                qtype,
                source=ItemSource.FINQA,
            )
        )
    return items


def write_passages(passages: dict[tuple[str, str], list[str]]) -> None:
    directory = ROOT / PASSAGES_DIRNAME
    if directory.exists():
        for stale in directory.glob("*.txt"):
            stale.unlink()
    directory.mkdir(parents=True, exist_ok=True)
    by_doc: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (doc_id, section), quotes in passages.items():
        by_doc[doc_id][section].extend(quotes)
    for doc_id, sections in by_doc.items():
        chunks = [f"# {doc_id}"]
        for section, quotes in sections.items():
            chunks.append(f"## {section}")
            seen: set[str] = set()
            for quote in quotes:
                if quote in seen:
                    continue
                seen.add(quote)
                chunks.append(quote)
        path = directory / passage_filename(doc_id)
        path.write_text("\n".join(chunks) + "\n", encoding="utf-8")


def main() -> None:
    passages: dict[tuple[str, str], list[str]] = defaultdict(list)
    items = handwritten(passages) + financebench(passages) + finqa(passages)
    write_passages(passages)
    out = ROOT / "v1.jsonl"
    lines = [row.model_dump_json(exclude_defaults=False) for row in items]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"wrote {len(items)} items to {out} and {len(list((ROOT / PASSAGES_DIRNAME).glob('*.txt')))} passages"
    )


if __name__ == "__main__":
    main()
