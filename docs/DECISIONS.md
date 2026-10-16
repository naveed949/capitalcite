# Decision log

This file is an append-only index of every architecture decision record in [`docs/decisions/`](decisions/). Newest rows go at the bottom. Do not edit an older row. The record remains the source of truth. This table is the lookup.

## How to add a decision

1. Add `docs/decisions/NNNN-slug.md` using the next unused number. Include Date, Status, and the decision.
2. Append one row to the log below. Do not edit or reorder older rows.
3. Every feature PR adds both the record and this row in the same change.

## Log

| ID | Date | Title | Decision | Record |
| --- | --- | --- | --- | --- |
| 0001 | 2026-10-15 | Project scaffold | Use uv, async FastAPI, Qdrant, and a Langfuse compose stack as the rails. RAG lands later. | [0001-scaffold.md](decisions/0001-scaffold.md) |
| 0002 | 2026-10-16 | EDGAR ingester | Talk to EDGAR with httpx. Filter on `reportDate`. Fetch extra submissions files. Store sidecar metadata next to each document. | [0002-edgar-ingester.md](decisions/0002-edgar-ingester.md) |
| 0003 | 2026-10-16 | Parse and three chunkers | Parse HTML/PDF to cleaned text. Keep tables as markdown. Chunk with fixed, recursive, or section-aware Item splits. Each chunk carries `doc_id`, `section`, `page`/`offset`, and `source_url`. | [0003-parse-and-chunk.md](decisions/0003-parse-and-chunk.md) |
| 0004 | 2026-10-16 | Golden Q&A set | Store typed JSONL items plus the excerpts the author read. `doc_id` is an accession when we have one. Validate quotes as substrings of passages and of parsed `document.md` (`--against-raw`). FinanceBench/FinQA are answer-scoring only. | [0004-golden-set.md](decisions/0004-golden-set.md) |
| 0005 | 2026-10-16 | Hybrid retrieval | Index `Chunk.text` into one Qdrant collection with named dense and BM25 vectors. Fuse with reciprocal rank fusion. Serve scores and citations from `POST /search`. | [0005-hybrid-retrieval.md](decisions/0005-hybrid-retrieval.md) |
| 0006 | 2026-10-16 | Retrieval eval | Score handwritten gold with span-overlap recall@k, MRR, and binary nDCG. Dense, BM25, and hybrid share the same items. Two chunkers write separate `config_hash` rows. RAGAS stays UNVERIFIED. | [0006-retrieval-eval.md](decisions/0006-retrieval-eval.md) |
