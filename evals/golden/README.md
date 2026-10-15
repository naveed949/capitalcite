# Golden Q&A set v1

This directory is the v1 evaluation set for capitalcite. Issue #6 will score
retrieval and answers against it. Nothing here is a model output.

## Files

| Path | Role |
|---|---|
| `v1.jsonl` | One item per line. Source of truth for questions and gold answers. |
| `passages/*.txt` | Excerpts the author actually read. Every `evidence.quote` is a substring of the passage for that `doc_id`. |
| `author.py` | Regenerates the JSONL and passages from local 10-K excerpts. CI does not run it. |

## Record shape

Each JSONL row is:

`id, question, answer, evidence[{doc_id, section, quote, role}], type, answerable, source`

`id`, `question`, `answer`, `evidence`, `type`, and `answerable` are the
issue #5 contract. `role` and `source` are extras for later evals.

- `doc_id` is the EDGAR accession for handwritten items over the ingested
  JPM / BAC / WFC / C 10-Ks. FinanceBench and FinQA items use the
  benchmark document name (those filings are not in the v1 ingest set).
- `section` is a 10-K Item (`Item 7`, `Item 8`, `body`) or a benchmark
  page label.
- `quote` is copied from a passage the author read. It is never invented.
- `role=answer` marks the span the gold answer is taken from.
  `role=relevant` marks a passage a retriever should still return
  (the other year in a comparison, a restated prior-year table).
- `type` is `extractive`, `numeric`, `comparison`, `multi_hop`,
  `unanswerable`, or `trick`.
- `answerable` is false for refusals. Those items still have an explicit
  refusal sentence in `answer`.
- `source` is `handwritten`, `financebench`, or `finqa`.

Retrieval metrics (#6) use `evidence` as the relevant-passage set.
Answer correctness uses `answer`. Do not treat a relevant-only quote as
the answer span.

## How items were made

On 2026-10-15 the author ran `capitalcite ingest --tickers JPM,BAC,WFC,C
--forms 10-K --years 2023-2025` and read the extracted text of those
twelve primary documents, plus SEC `companyfacts` for the four CIKs.

Handwritten questions were written from those passages. Numeric answers
were checked against the same 10-K table or sentence and, where the
concept exists in `companyfacts`, against the FY 10-K fact for that
accession.

Wells Fargo's ingested primary HTML is an iXBRL shell (FY2025 is a cover
page). Those files do not state CET1 or net income. The set records that
gap as unanswerable items instead of filling numbers from `companyfacts`
without a quote.

FinanceBench items are a filtered subset of the public
`financebench_open_source.jsonl` (JPMorgan, American Express, 3M, Boeing).
FinQA items are a filtered subset of `ibm-research/finqa` train
(Citi, Goldman, JPM, Morgan Stanley). Evidence text was read from those
datasets and stored as passages.

## Mix (v1)

Validated by `capitalcite golden validate`:

- 108 items
- at least 15% unanswerable or trick
- at least 30% numeric (`numeric` or `comparison`)
- handwritten majority over the twelve ingested 10-Ks
- FinanceBench and FinQA calibration tails

## Check

```bash
uv run capitalcite golden validate
```

No network. The command fails if a quote is missing from its passage, if
an id repeats, or if the mix targets are missed.

## Leakage and size

About 100 items is large enough to cover four banks, three fiscal years,
numeric vs extractive vs refusal, and a published-benchmark tail. It is
small enough that every quote can be re-read.

Questions do not paste the gold number. Comparison items label both
filings as evidence so a retriever is not scored on one year only.
Benchmark items keep their original wording; that is a known leakage
risk if a model saw FinanceBench or FinQA in training. They are for
harness calibration, not for claiming a new SOTA.

Full 10-K HTML is not committed (tens of megabytes, gitignored under
`data/`). The passages are short excerpts.
