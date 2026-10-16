# Section labels on the twelve ingested 10-Ks

Measured by running `detect_sections` on parsed `document.md` text
from the live EDGAR files under `data/raw/edgar/`. Before is main
at `647b628`. After is this change.

A gold span is the handwritten evidence offset already stored in
`evals/golden/v1.jsonl`.

## Before

| Filing | Sections | Item 7 chars | Gold CET1 span label |
| --- | --- | --- | --- |
| BAC FY2023 | body, 1, 1A, 1B, 1C, 2, 3, 4, 5, **1A again**, 7A, 9–16. No Item 7. | 0 | Item 1A |
| BAC FY2024 | same shape | 0 | Item 1A (641772) |
| BAC FY2025 | same shape | 0 | Item 1A |
| C FY2023 | body only | 0 | body (372211) |
| C FY2024 | body only | 0 | body |
| C FY2025 | body only | 0 | body |
| JPM FY2023 | body, 1–15. Wrapper Item 7 is 396 chars. Item 15 is 1.34M chars. | 396 | Item 15 |
| JPM FY2024 | same. Wrapper Item 7 is 396 chars. Item 15 is 1.31M chars. | 396 | Item 15 (487805) |
| JPM FY2025 | same | 396 | Item 15 |
| WFC FY2023 | body only | 0 | body |
| WFC FY2024 | body only | 0 | body |
| WFC FY2025 | body only | 0 | body (228593) |

JPM's wrapper Item 7 is the Form 10-K pointer "appears on pages 52–167".
The CET1 sentence lives in the attached annual report, which the old
detector left inside Item 15. WFC and C never say `Item 7` in the HTML.
BAC's real heading is a markdown table row, so `^Item` missed it.

## After

| Filing | Sections | Gold CET1 span label |
| --- | --- | --- |
| BAC FY2023–FY2025 | body, Item 1–16 in order, including Item 7 | Item 7 |
| C FY2023–FY2025 | body, several wrapper items, then Item 7 and Item 8 | Item 7 |
| JPM FY2023–FY2025 | wrapper items 1–5 and 7A–15, then Item 7 at the real MD&A | Item 7 |
| WFC FY2023–FY2025 | body, Item 7 (`Financial Review`) | Item 7 |

79 of 94 handwritten evidence rows moved onto Item 7. 15 JPM selected-financials
rows stay Item 15 because they sit before the MD&A heading in the attached report.

## Why the labels were wrong

1. A line that starts with `Item 7` but continues `appears on pages N–M` is a pointer, not the MD&A.
2. `Management's discussion and analysis` as a 12pt heading, followed by `The following is Management's discussion…`, is the real Item 7. The same short title on later pages is a running header.
3. `| Item 7. Bank of America Corporation and Subsidiaries |` is a heading. `| Item 7. | title | 25 |` is the TOC.
4. C's body title is the all-caps MD&A phrase inside a table. WFC's is `Financial Review`.
