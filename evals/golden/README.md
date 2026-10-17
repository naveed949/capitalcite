# Golden Q&A set v1

This directory is the v1 evaluation set for capitalcite. Issue #6 will score
retrieval and answers against it. Nothing here is a model output.

## Files

| Path | Role |
|---|---|
| `v1.jsonl` | One item per line. Source of truth for questions and gold answers. |
| `ci.jsonl` | Three fixture-aligned items for `capitalcite check`. Not part of the published 112-item mix. |
| `passages/*.txt` | Excerpts the author actually read. Every `evidence.quote` is a substring of the passage for that `doc_id`. |
| `author.py` | Original excerpt-based generator. Do not re-run it over v1: the committed JSONL was rebuilt from parsed `document.md` after the WFC primary-document fix. CI does not run it. |

## Record shape

Each JSONL row is:

`id, question, answer, evidence[{doc_id, section, quote, role, start, end}], type, answerable, source`

`id`, `question`, `answer`, `evidence`, `type`, and `answerable` are the
issue #5 contract. `role`, `source`, and `start`/`end` are extras for
later evals.

- `doc_id` is the EDGAR accession for handwritten items over the ingested
  JPM / BAC / WFC / C 10-Ks. FinanceBench and FinQA items use the
  benchmark document name (those filings are not in the v1 ingest set).
- `section` is what the #22 section detector reports at the quote offset
  (`Item 7`, `Item 8`, `Item 15`, `Item 1A`, `body`), or a benchmark
  page label.
- `quote` is copied from a passage the author read. It is never invented.
- `start` / `end` are half-open offsets in that filing's parsed
  `document.md` (the same coordinates as `Chunk.start` / `Chunk.end`).
  Handwritten rows have them. FinanceBench and FinQA rows omit them.
- `role=answer` marks the span the gold answer is taken from.
  `role=relevant` marks a passage a retriever should still return
  (the other year in a comparison, a restated prior-year table).
- `type` is `extractive`, `numeric`, `comparison`, `multi_hop`,
  `unanswerable`, or `trick`.
- `answerable` is false for refusals. Those items still have an explicit
  refusal sentence in `answer`.
- `source` is `handwritten`, `financebench`, or `finqa`.
- `GoldenItem.in_recall` is true only for handwritten items.

Retrieval metrics (#6) use handwritten `evidence` as the relevant-passage
set. FinanceBench and FinQA are excluded from recall@k; they exist for
answer-scoring calibration only. Answer correctness uses `answer` for
every source. Do not treat a relevant-only quote as the answer span.

## How items were made

The author ran `capitalcite ingest --tickers JPM,BAC,WFC,C --forms 10-K
--years 2023-2025`. Ingest reads each filing's `index.json` and, when
SEC's declared `primaryDocument` is a small cover/exhibit shell, stores
the largest eligible main 10-K `.htm` instead. That is required for
Wells Fargo: FY2025 declares `wfc-20251231_d2.htm` (~360KB); the real
report is `wfc-20251231.htm` (~11.6MB) and states Standardized CET1
10.61%, LCR 119%, and net income 21,338. FY2023/FY2024 invert the names
(the real files are `*_d2.htm`).

Handwritten quotes and spans were taken from the parsed `document.md`
that `capitalcite chunk` writes for those twelve filings, then checked
with `capitalcite golden validate --against-raw`. Numeric answers were
checked against the same sentence or table.

FinanceBench items are a filtered subset of the public
`financebench_open_source.jsonl` (JPMorgan, American Express, 3M, Boeing).
FinQA items are a filtered subset of `ibm-research/finqa` train
(Citi, Goldman, JPM, Morgan Stanley). Evidence text was read from those
datasets and stored as passages. They are not aligned to the ingested
corpus and are not in recall@k.

## Mix (v1)

Validated by `capitalcite golden validate`:

```
golden items=112 answerable=94 unanswerable_or_trick=18 unanswerable_or_trick_pct=0.161 numeric=85 numeric_pct=0.759 handwritten=89 financebench=13 finqa=10
```

- at least 15% unanswerable or trick
- at least 30% numeric (`numeric` or `comparison`)
- handwritten majority over the twelve ingested 10-Ks
- FinanceBench and FinQA calibration tails (answer-scoring only)

## Check

```bash
uv run capitalcite golden validate
uv run capitalcite golden validate --against-raw "$DATA_DIR"
```

The first command is offline. It fails if a quote is missing from its
passage, if a handwritten row lacks `start`/`end`, if an id repeats, or
if the mix targets are missed.

The second command needs a local ingest tree. It matches each
handwritten quote against that filing's parsed `document.md` and checks
the stored spans. FinanceBench and FinQA are skipped.

## Leakage and size

About 100 items is large enough to cover four banks, three fiscal years,
numeric vs extractive vs refusal vs multi-hop, and a published-benchmark
tail. It is small enough that every quote can be re-read.

Questions do not paste the gold number. Comparison items label both
filings as evidence so a retriever is not scored on one year only.
Benchmark items keep their original wording; that is a known leakage
risk if a model saw FinanceBench or FinQA in training. They are for
harness calibration, not for claiming a new SOTA.

Full 10-K HTML is not committed (tens of megabytes, gitignored under
`data/`). The passages are short excerpts. Spans point back into a
local parsed document.
