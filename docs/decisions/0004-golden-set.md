# 0004. Golden Q&A set

Date: 2026-10-16
Status: Accepted

Issue #5 needs a set of questions a later retriever and answerer can be
scored against. The hard constraint is that every gold answer is grounded
in a filing passage the author actually read. This record is why the set
is a typed JSONL plus a passage store, why handwritten evidence also
carries character spans from parsed `document.md`, and why Wells Fargo
items are written from the real 10-K HTML rather than the cover page
SEC lists as `primaryDocument`.

## A golden item is two labels, not one

A later eval (#6) will ask two different questions of the same row.

Retrieval asks: did we surface the passages a human marked relevant?
That is recall@k / MRR / context recall. The label is `evidence[]`.

Answer correctness asks: is the system's string faithful to the gold
`answer`? That is numeric exact-match, faithfulness, citation accuracy.
The label is `answer`, supported by the evidence rows whose `role` is
`answer`.

A comparison such as "did CET1 rise from 2024 to 2025" has two relevant
filings. Only one quote contains the 2025 figure the answer names. Both
quotes must be retrieved. Collapsing those into a single "the answer
text" field would make a retriever look worse than it is, or let an
answerer look better than it is.

Unanswerable and trick items test refusals. They still carry an explicit
refusal sentence so a later scorer is not left to guess what "I don't
know" should look like.

## Dual store, then a check against the parsed filing

Two shapes were on the table for the committed files.

JSONL-only: the quote is the passage. Smaller tree. A typo in a quote
cannot be caught, because there is nothing else to compare it to. The
author's "I read this filing" claim becomes unfalsifiable.

Passages plus items: each `doc_id` has a committed excerpt file. Every
`evidence.quote` must be a substring of that file, and the file must
contain the section heading. `capitalcite golden validate` fails
otherwise. CI can enforce "never invented" without opening EDGAR.

We shipped the dual store. That is still not enough for recall@k.
Passages are excerpts the author chose. If the only grounding check is
"quote ⊆ excerpt we wrote", the set is circular: we could invent a
number, paste it into both files, and pass CI.

`capitalcite golden validate --against-raw <data_dir>` closes that loop
for handwritten items. It loads the same parsed `document.md` the #22
chunker writes (or parses the raw HTML with that parser), finds each
quote, and records `start`/`end` on the evidence row. Those offsets are
the same coordinate system as `Chunk.start` / `Chunk.end`, so #6 can
score recall@k by span overlap instead of string guessing.

FinanceBench and FinQA rows are excluded from recall@k. Their `doc_id`
is a benchmark document name, not an ingested accession, and those
filings are not in the v1 corpus. They exist for answer-scoring
calibration only. `--against-raw` skips them (`skipped_benchmark=`,
`recall=handwritten-only`). `GoldenItem.in_recall` is true only for
`source=handwritten`.

The public surface is still one command. Mix math, accession vs
benchmark ids, and raw alignment stay behind `validate_golden_set` and
`validate_against_raw`.

## `doc_id` is an accession when we have one

Ingest names a filing by accession (`Filing.accession`). #22 (merged)
sets `Chunk.doc_id` to that accession. Handwritten evidence uses the
same string so #4 and #6 can join a retrieved chunk to a gold passage
without a mapping table.

FinanceBench and FinQA items are over filings that are not in the v1
ingest set. Their `doc_id` is the benchmark document name. The README
says so. Pretending those names are accessions would be a lie.

## The declared primary is not always the 10-K

SEC `primaryDocument` for Wells Fargo FY2025 is
`wfc-20251231_d2.htm` (~360KB cover/exhibit shell). The real report in
the same filing is `wfc-20251231.htm` (~11.6MB) and states the numbers
the review named (Standardized CET1 10.61%, LCR 119%, net income
21,338). FY2023 and FY2024 invert the names: the declared primary is
the smaller shell and the real report is `*_d2.htm`.

The ingester now reads `index.json` and, when a larger eligible main
10-K `.htm` exists, stores that file instead. Eligible means HTML that
is not an exhibit, not `R*.htm`, and not `*-index.htm`. Golden WFC
items were rewritten from that parsed text. Cover-page artifacts
(`cc-u-001`, `cc-u-002`, `cc-u-009`, `cc-t-005`, `cc-h-063`–`066`)
are gone. Genuine refusals keep unanswerable-or-trick at or above 15%.

We still do not fill a number from `companyfacts` without a quote in
the ingested document.

## Taxonomy and mix

`type` is a closed enum: extractive, numeric, comparison, multi_hop,
unanswerable, trick. Free-text types become a second undocumented
taxonomy by the third contributor.

The issue's floors (15% unanswerable or trick, 30% numeric) are checked
on load. Numeric means `numeric` or `comparison`. Comparison is a number
question that needs two passages.

About 100 items covers four banks, three years, refusals, multi-hop
joins, and a benchmark tail. A 1,000-item set would force generated
questions and invented quotes. A 20-item set would not support a
chunking × embedding matrix.

## Alternatives considered

| Choice | Why not |
|---|---|
| JSONL-only quotes | No independent check that the author read a filing. |
| Ground only against excerpts we wrote | Circular: invent a number, paste it in both places, CI passes. |
| One file per source | Three lists to update for one mix invariant. |
| Commit full 10-K HTML | Tens of megabytes, gitignored for a reason. Excerpts plus `--against-raw` against a local corpus are enough. |
| Fill WFC numbers from companyfacts | Facts without a read passage violate the issue's grounding rule. |
| Trust SEC `primaryDocument` | WFC FY2023–FY2025 declare a cover/exhibit shell. |
| Put FinanceBench/FinQA in recall@k | Those `doc_id`s are not ingest accessions and have no chunks. |
| LangChain / HuggingFace dataset objects | Own the item type. Pull a stack #6 has not chosen. |

## What this issue does not do

It does not run RAGAS, recall@k, or `capitalcite eval`. That is #6.
It does not download EDGAR in CI. The passages and spans are already
in the repo. `--against-raw` is a local check against an ingested
corpus; verify.sh runs it after a live ingest, or marks it UNVERIFIED.
