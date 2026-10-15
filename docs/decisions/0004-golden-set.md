# 0004. Golden Q&A set

Date: 2026-10-16
Status: Accepted

Issue #5 needs a set of questions a later retriever and answerer can be
scored against. The hard constraint is that every gold answer is grounded
in a filing passage the author actually read. This record is why the set
is a typed JSONL plus a passage store, not a spreadsheet of invented
numbers.

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

## Dual store: passages plus items

Two shapes were on the table.

JSONL-only: the quote is the passage. Smaller tree. A typo in a quote
cannot be caught, because there is nothing else to compare it to. The
author's "I read this filing" claim becomes unfalsifiable.

Passages plus items: each `doc_id` has a committed excerpt file. Every
`evidence.quote` must be a substring of that file, and the file must
contain the section heading. `capitalcite golden validate` fails
otherwise. CI can enforce "never invented" without opening EDGAR.

We shipped the dual store. The public surface is one command. The
complexity (substring checks, mix math, accession vs benchmark doc ids)
stays behind `validate_golden_set`.

Split files per source (handwritten / FinanceBench / FinQA) would make
provenance obvious on disk. They would also create three lists that
must stay in sync with the mix targets. One JSONL plus a `source` field
keeps a single stream. Stats are derived.

## `doc_id` is an accession when we have one

Ingest already names a filing by accession (`Filing.accession`). #22 (merged) sets `Chunk.doc_id` to that accession. Handwritten evidence
uses the same string so #4 and #6 can join a retrieved chunk to a gold
passage without a mapping table.

FinanceBench and FinQA items are over filings that are not in the v1
ingest set. Their `doc_id` is the benchmark document name. The README
says so. Pretending those names are accessions would be a lie.

Wells Fargo's ingested primary HTML does not contain CET1 or net income.
`companyfacts` has those numbers. We did not write WFC CET1 items from
facts without a quote. Unanswerable rows record the gap.

## Taxonomy and mix

`type` is a closed enum: extractive, numeric, comparison, multi_hop,
unanswerable, trick. Free-text types become a second undocumented
taxonomy by the third contributor.

The issue's floors (15% unanswerable or trick, 30% numeric) are checked
on load. Numeric means `numeric` or `comparison`. Comparison is a number
question that needs two passages.

About 100 items covers four banks, three years, refusals, and a
benchmark tail. A 1,000-item set would force generated questions and
invented quotes. A 20-item set would not support a chunking × embedding
matrix.

## Alternatives considered

| Choice | Why not |
|---|---|
| JSONL-only quotes | No independent check that the author read a filing. |
| One file per source | Three lists to update for one mix invariant. |
| Commit full 10-K HTML | Tens of megabytes, gitignored for a reason. Excerpts are enough to ground a quote. |
| Fill WFC numbers from companyfacts | Facts without a read passage violate the issue's grounding rule. |
| LangChain / HuggingFace dataset objects | Own the item type. Pull a stack #6 has not chosen. |
| Wait for PR #22 to name chunks | The golden set has to land on main. Accession is already the ingest identity. |

## What this issue does not do

It does not run RAGAS, recall@k, or `capitalcite eval`. That is #6.
It does not download EDGAR in CI. The passages are already in the repo.
