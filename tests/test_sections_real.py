"""Heading detector against real EDGAR markdown snippets."""

from __future__ import annotations

from capitalcite.parse.sections import detect_sections, section_at

# Parsed markdown copied from the ingested 10-Ks on 2026-10-16.

JPM_WRAPPER = """\
Item 6. Reserved
Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations.
Management’s discussion and analysis of financial condition and results of operations, entitled “Management’s discussion and analysis,” appears on pages 52–167. Such information should be read in conjunction with the Consolidated Financial Statements and Notes thereto, which appear on pages 172–321.
Item 7A. Quantitative and Qualitative Disclosures About Market Risk.
Refer to the Market Risk Management section of Management’s discussion and analysis on pages 141–149.
Item 8. Financial Statements and Supplementary Data.
The Consolidated Financial Statements, together with the Notes thereto, appear on pages 172–321.
Item 15. Exhibits, Financial Statement Schedules.
Exhibit index follows.
"""

JPM_REAL_MDA = """\
| JPMorgan Chase & Co./2024 Form 10-K | | 51 | | | | | | |

Management’s discussion and analysis
The following is Management’s discussion and analysis of the financial condition and results of operations (“MD&A”) of JPMorganChase for the year ended December 31, 2024.
Selected capital and other metrics
the Standardized and Advanced CET1 ratios were 15.7% and 15.8%, respectively.

Management’s discussion and analysis
Three lines of defense
The Firm’s “three lines of defense” are as follows.
"""

BAC_TOC_AND_BODY = """\
| Item 6. | [Reserved] | 24 | | | | | | |
| Item 7. | Management’s Discussion and Analysis of Financial Condition and Results of Operations | 25 | | | | | | |
| Item 7A. | Quantitative and Qualitative Disclosures about Market Risk | 86 | | | | | | |
| Item 8. | Financial Statements and Supplementary Data | 87 | | | | | | |

| Item 7. Bank of America Corporation and Subsidiaries | | | | | | | | | | | | | | |
| Management's Discussion and Analysis of Financial Condition and Results of Operations | | | | | | | | | | | | | | |
| Table of Contents | | | | | | | | | | | | | | |

CET1 capital ratio of 11.8 percent under the Standardized approach
"""

C_TOC_AND_BODY = """\
| 7. | | Management’s Discussion and Analysis of Financial Condition and Results of Operations | 7–32, 70–129 | | | | | | | | |
| 7A. | | Quantitative and Qualitative Disclosures About Market Risk | 70–129 | | | | | | | | |

| 6 | MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION | AND RESULTS OF OPERATIONS | EXECUTIVE SUMMARY | As described further throughout this Executive Summary, CET1 increased to 13.4% as of December 31, 2023 |
"""

WFC_TOC_AND_BODY = """\
| | | | | Financial Review | | | | | | | | | | | | | | | | | | | | | | | | | | | | |
| 2 | | | | Overview | | 114 | | 4 | | | Equity Securities | | | | | | | | | | | | | |

This Annual Report, including the Financial Review and the Financial Statements and related Notes, contains forward-looking statements.

Financial Review

| | Overview | |
Wells Fargo & Company is a leading financial services company.
our Common Equity Tier 1 (CET1) ratio was 10.61% under the Standardized Approach
"""


def test_jpm_wrapper_item7_is_a_pointer_not_the_mda() -> None:
    text = JPM_WRAPPER + "\n" + JPM_REAL_MDA
    spans = detect_sections(text)
    names = [span.name for span in spans]
    assert "Item 7" in names
    item7 = next(span for span in spans if span.name == "Item 7")
    assert "15.7%" in text[item7.start : item7.end]
    assert "appears on pages 52" not in text[item7.start : item7.end]
    assert "Three lines of defense" in text[item7.start : item7.end]


def test_bac_table_item7_not_toc_row() -> None:
    spans = detect_sections(BAC_TOC_AND_BODY)
    item7 = next(span for span in spans if span.name == "Item 7")
    assert "11.8 percent" in BAC_TOC_AND_BODY[item7.start : item7.end]
    assert BAC_TOC_AND_BODY[item7.start :].startswith("Item 7. Bank of America")


def test_c_allcaps_table_title_is_item7() -> None:
    spans = detect_sections(C_TOC_AND_BODY)
    item7 = next(span for span in spans if span.name == "Item 7")
    assert "13.4%" in C_TOC_AND_BODY[item7.start : item7.end]


def test_wfc_financial_review_is_item7() -> None:
    spans = detect_sections(WFC_TOC_AND_BODY)
    item7 = next(span for span in spans if span.name == "Item 7")
    assert "10.61%" in WFC_TOC_AND_BODY[item7.start : item7.end]
    assert section_at(spans, WFC_TOC_AND_BODY.index("10.61%")) == "Item 7"


def test_existing_bare_item_lines_still_work() -> None:
    text = "Preface\nItem 7. MD&A\nCash is up.\nItem 8. Statements\nNotes follow."
    assert [span.name for span in detect_sections(text)] == ["body", "Item 7", "Item 8"]
