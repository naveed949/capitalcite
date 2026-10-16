# 0006. Retrieval eval

Date: 2026-10-16

Status: Accepted

Issue #6 needs a number for "did the retriever surface the gold
passage?" on the same handwritten items, across dense, BM25, and hybrid,
and across at least two chunkers. This record is why the label is span
overlap, why the three metrics sit side by side, and why RAGAS is not in
the committed table.

## The label is a span, not a section name

A gold row already stores `doc_id`, `start`, and `end` in parsed
`document.md`. A chunk already stores the same three fields. A hit is
relevant when the accession matches and the half-open ranges overlap:
`chunk.start < evidence.end` and `evidence.start < chunk.end`. That is
`span_overlap` in `retrieval/sanity.py`. The eval does not grow that
module. Quote-index sanity still indexes each gold quote as its own
chunk. That number is lexical overlap between the question and the
quote. It is not corpus recall.

Section strings are citation metadata. The detector that writes them had
to be fixed in this change, because every WFC chunk was `body` and JPM's
capital paragraph sat in Item 15. Offsets did not move. Handwritten
`evidence.section` was retagged so `golden validate --against-raw` still
holds. The scorer never reads the section string.

FinanceBench and FinQA rows have no start/end. They stay out of the
denominator. `GoldenItem.in_recall` is handwritten-only. An item enters
the suite when it is in recall and has at least one spanned evidence
row.

## Three metrics, one ranking

Recall@k is the fraction of items with any relevant hit in the top k.
It does not care whether gold is first or fifth.

MRR@k is the mean of `1/rank` of the first relevant hit. A miss past k
is 0. It rewards putting gold first.

nDCG@k credits each gold span once, at the first hit that covers it,
then divides by the ideal DCG of `min(k, gold_span_count)` documents at
the top, then clamps at 1. Two overlapping hits on one span do not add
a second gain. A rank-2 first cover scores `1 / log2(3)`, not 0.5.
There is no graded relevance.

The published cutoff is k=5, the window the #24 baseline talked about.
Retrieve still walks 10 so a rank-8 miss such as `cc-h-013` is visible
as `first_rank` and in MRR@10 / nDCG@10. Filters stay off. A ticker
filter is a different condition. The #24 rank-8 observation under a JPM
filter is explained by the unfiltered `first_rank`, not by a second
matrix axis.

## Same questions, hashed indexes

Dense, BM25, and hybrid fail on different queries. A comparison that
does not hold the questions fixed cannot say which mode is better. The
chunker is the other axis. Section and fixed share the same gold
offsets. Recursive is not in the default matrix. Adding it would be a
third re-chunk and a third embed.

`IndexConfig` already hashes embedder id, dense width, chunker, size,
overlap, and prefix. Each arm writes that hash onto the row. The result
file is `evals/results/<sha12>.json` (or `<sha12>-small.json`). The
markdown sibling is generated from that JSON. README numbers come from
the same object. They are not typed by hand.

`--small` is the CI path. It chunks
`tests/fixtures/chunking/corpus`, embeds with `FakeEmbedder`, and opens
`:memory:` Qdrant. It does not download a model and it does not take
`--embedder` or `--input-dir`. Full mode re-chunks the raw filings into
a private work tree so two chunkers do not share one `chunks.jsonl`.
`QDRANT_PATH` or a temp directory holds the embedded store. A missing
corpus or a failed embedder is exit 2, which verify records as
UNVERIFIED.

## What this leaves out

RAGAS and DeepEval need an LLM judge and a key. Those rows stay
UNVERIFIED. Hybrid plus a cross-encoder is not wired. That row stays
UNVERIFIED. Numeric exact-match and citation accuracy on generated
answers belong to `#7`. This harness scores retrieval only.

About 77 handwritten spanned items is a small sample. Moving one item
changes recall by about `1/n`. A 0.02 gap is noise. The table reports n
and does not rank chunkers on a gap that small.

## The first BM25 matrix measured TF-IDF

The first matrix had section BM25 Recall@5 = 0/77 and hybrid worse than
dense. A probe on `cc-h-013`, `cc-h-063`, and `cc-h-001` showed the
sparse path was hashed raw term counts plus Qdrant IDF: no k1
saturation, no b length norm, no stopwords. Query words `what` / `was`
/ `the` / `at` carried weight 1. Citi capital tables that repeat
`CET1` / `capital` / `ratio` outranked the gold JPM and WFC Item 7
chunks (ranks 172–389). That is TF-IDF, not BM25. It was a retrieval
bug on the #4 production path, not a property of the corpus.

The encoder is now FastEmbed `SparseTextEmbedding("Qdrant/bm25")` for
every production embedder (bge-small, minilm, openai-small).
`embed_documents` calls `embed()`. `embed_query` calls `query_embed()`
on the raw question. `IndexConfig.sparse_id` records
`fastembed:Qdrant/bm25:k1=1.2:b=0.75:avgdl=256` so a hashed-count
collection cannot be reused. Pytest `FakeEmbedder` uses the same
formula and does not download FastEmbed. The eval writes a BM25 probe
for those three items next to the matrix JSON.

minilm is UNVERIFIED: a second full embed of 12k–17k chunks is not
cheap on this corpus. On the corrected encoder, section BM25
Recall@5 is 0.182 (was 0.000 under TF-IDF) and hybrid beats dense on
both chunkers. The gold Item 7 sentence for a JPM CET1 question can
still sit past rank 10 because that sentence names CET1 once and the
filing often says "the Firm"; JPM capital tables now lead those
queries. The full table lives in `evals/results/<sha12>.json`.
