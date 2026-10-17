# Cited answers over bank 10-Ks

I am Naveed Iqbal. I work as a blockchain engineer at Kaia, mostly in
Rust, Solidity, and zero-knowledge proofs. I have spent 8 years on
systems that move money. I am moving into applied AI. This write-up
is about [capitalcite](https://github.com/naveed949/capitalcite), a
system I built to answer questions over U.S. bank 10-Ks and to refuse
when the filing does not support the answer.

## The problem

A 10-K is a book. JPMorgan, Bank of America, Wells Fargo, and Citi
each file one every year. CET1 capital, net income, and stress-capital
buffers sit in Item 7 and Item 8, often next to a table. An analyst
question sounds simple. What was JPMorgan's Standardized CET1 ratio
at year-end 2024? The wrong answer is a fluent paragraph that names
a neighboring bank's ratio, or last year's ratio, or a figure the
model invented.

I wanted every answer to point at a span in a parsed filing, and a
missing span to be a refusal rather than a guess. The corpus is
twelve 10-Ks. The golden set is 112 questions. Recall is scored on
77 handwritten items that have `start`/`end` in `document.md`.
FinanceBench and FinQA tails stay out of recall@k. Answer quality
is UNVERIFIED without an `OPENAI_API_KEY`.

## Chunking is a citation problem first

A chunk here is a cited span, not a bag of tokens. `doc_id` is the
EDGAR accession. `section` is the 10-K Item at the start of the span.
`start` and `end` are a half-open range into parsed `document.md`.
If those three fields are wrong, later retrieval cannot prove a quote
came from the filing.

A fixed window of 512 tokens with 64 tokens of overlap is easy to
reproduce. It is also blind. It will cut a markdown table in half.
It will start in Item 7 and end in Item 8 and still be labeled Item 7.

Section-aware splitting cuts on Item headings first. An Item that
fits the budget stays one chunk. An Item that does not is split with
the recursive splitter, and every piece keeps that Item name.
Markdown tables are atomic. Overflow tables split on rows with the
header repeated in `header_prefix`. Token budget is still a
four-character heuristic, not the embedder's tokenizer. Recursive
splitting exists in the CLI. It is not a third default eval arm.

## Dense, BM25, and fusion by rank

Dense search with FastEmbed `bge-small` (`BAAI/bge-small-en-v1.5`)
places a chunk near paraphrases. It misses a rare token the question
names exactly when the HNSW walk never visits that point. BM25 does
the opposite. Production sparse vectors are FastEmbed `Qdrant/bm25`
(`k1=1.2`, `b=0.75`). Term frequency, rarity, and length.

Those scores are not in the same unit. Reciprocal rank fusion ignores
the raw scores and adds `1 / (k + rank)` per list. `RRF_K` defaults
to 60. Qdrant's rank index starts at 0, so a first-place hit on one
leg scores `1/k`. The server is sent Qdrant's `k`, not `k+1`.
Production hybrid is Qdrant `RrfQuery`. The in-repo paper formula is
for tests.

There is no reranker on the serve path. The hybrid-plus-rerank row
in the README is UNVERIFIED until an eval writes it. The first BM25
matrix in this project was hashed term counts plus IDF. Section BM25
Recall@5 was 0. `sparse_id` sits inside `IndexConfig`, so that
collection cannot be reused as BM25.

## Ask, agent, gate, MCP, graph

`POST /ask` with `path=rag` retrieves hybrid. The model names a
passage index and a quote. `ground()` copies `doc_id`, `start`, and
`end` from that hit. If the quote is not a verbatim substring, the
whole answer is refused. There is no second generation.
`ASK_MODEL=fake` quotes a hit and does not read `OPENAI_API_KEY`.
`ASK_MODEL=openai` without a key fails closed. Live OpenAI quality
is UNVERIFIED.

`path=agent` is a LangGraph plan, then gated tools, then the same
`ask()` path. It is not ReAct. The measured planner is
`FakeAgentModel`. One `search_filings` per named ticker and year.
More retrieves, not a smarter generator.

The planner is untrusted. The policy file is the allow-list. Default
is deny. Unknown names, extra keys, a broken policy, or a failed
audit write never call the runner. The stock MCP SDK would drop extra
keys before the handler. This server overrides `call_tool` so
`{"query":"x","cmd":"ls"}` stays `invalid_args`.

The audit log is hash-chained JSONL plus a sibling `.tip`. Args are
digested. The gate decision is written before the tool runs. The
chain is not HMAC. Anyone who can write the directory can rebuild it.
`capitalcite mcp` advertises `search_filings` and `ask` on stdio.
`path=agent` stays on HTTP.

Hybrid search cannot walk `PARENT_OF`. `graph_query` is a typed
tool, not Cypher from the model. Blank `NEO4J_URI` returns
`graph not configured`. Graph neighbors are not fused into citations.

Azure Container Apps Bicep is in the repo. Live apply is UNVERIFIED.

## What is actually measured

Twelve 10-Ks, FastEmbed `bge-small`, span-overlap on 77 handwritten
items, from
[`evals/results/7388ac3eea43.json`](../evals/results/7388ac3eea43.json).

Section/hybrid Recall@5 is 0.169. Fixed/hybrid is 0.195. n=77 means
one item is 0.013 recall. A 0.02 gap is two items. That is noise.
Section BM25 at 0.182 versus section dense at 0.078 is eight items.
That is the gap that survives the noise floor.

On comparison and multi-hop items, n=15, the fake planner's Recall@5
is 0.400 against plain RAG at 0.267. Retrieval only. Answer quality
stays UNVERIFIED.

The graph suite is n=6. Fixture-corpus substring hit rate is 0.000.
`graph_query` on the memory snapshot is 1.000.

Red-team on the gate is 5/5. Audit verify is 3 cases at pass rate
1.00. `capitalcite check` gates quote-index sanity at 36/77 and
fixture span-overlap at 3/3. Quote-index sanity is not corpus
Recall@5. Nightly allows one item of rank-boundary noise on the
section/hybrid cell.

Latency is `ASK_MODEL=fake` on the 12-point fixture. RAG p50 is
14 ms, p95 16 ms. Agent p50 and p95 are both 21 ms. Gate `decide()`
p95 is 0.020 ms on 2000 samples. A fake-model clone timed at
11 seconds on a 4-core VM with warm `uv` and Hugging Face caches.
A cold `bge-small` download was 67 MB.

## Why section/hybrid Recall@5 is 0.169

The scorer is span overlap on `doc_id` plus half-open offsets. A hit
at 5 means any gold span appeared in the top 5. The section string
is metadata. The scorer never reads it. Two-span comparison questions
still get a single rank in `covered_at` when only one gold span lands.

Section/hybrid is 13/77 at k=5 in
[`7388ac3eea43.json`](../evals/results/7388ac3eea43.json). Hits that
did land include JPMorgan CET1 capital (`cc-h-015`, `cc-h-027`) and
Advanced CET1 ratio (`cc-h-026`) phrases in Item 7, JPMorgan's SCB
requirement (`cc-h-036`), and Citigroup average deposits
(`cc-h-050`). None of those five gold quotes is a net-income phrase.
Comparison hits such as `cc-h-052` and `cc-h-070` still cover only
one of the two gold spans inside the top 10.

Ten items have a first gold hit between rank 6 and rank 10.
`cc-h-008`, `cc-h-016`, `cc-h-024`, `cc-h-025`, `cc-h-034`,
`cc-h-038`, `cc-h-040`, `cc-h-049`, `cc-h-059`, `cc-t-001`. A
reranker could recover that band. It would not recover the empty
`covered_at` rows. Those are the majority.

The section BM25 diagnose file for `cc-h-013`, `cc-h-063`, and
`cc-h-001`
([`7388ac3eea43-bm25-diagnose.md`](../evals/results/7388ac3eea43-bm25-diagnose.md))
shows the texture. Hybrid `covered_at` for those three items is empty
in [`7388ac3eea43.json`](../evals/results/7388ac3eea43.json), so the
gold span is absent from the top 10.

The sparse query keeps the bank name (`jpmorgan` on the JPMorgan
items, `well` and `fargo` on Wells Fargo). The gold quote is missing
those name terms (`gold_quote_missing_query_terms`). Overlap with the
gold quote is `cet1` on `cc-h-013` and `cc-h-001`. On `cc-h-063` it
is `cet1`, `ratio`, and `approach`.

For `cc-h-013`, BM25's top 5 is four JPMorgan chunks (regulatory-note
tables and other years) and one Citigroup chunk. I do not have a
committed per-query dense or hybrid hit list for these items. I will
not claim that dense search failed to save them. I will not claim that
RRF fused two lists that already agreed on the wrong bank.

Three fixes follow, and the README already names them. A rerank arm
on the same 77 items, with p95 latency next to Recall@5. If the lift
is inside the 0.013 noise floor, leave rerank off. Better chunk
boundaries, including late chunking, so a table and the sentence that
glosses it stay together. Table-aware PDF parsing. HTML tables are
atomic. PDF tables are not. I would not fuse graph neighbors into
every `retrieve()`. Identity joins are a tool, not a ranking signal
for CET1 spans.

The limiter on Recall@5 is still retrieval. Until a reranker or a
better parse moves those empty `covered_at` rows, I will keep saying
0.169 instead of pretending the agent loop fixed ranking.

## LinkedIn version

I built [capitalcite](https://github.com/naveed949/capitalcite), a
cited-answer system over twelve U.S. bank 10-Ks, as I move from
blockchain engineering at Kaia into applied AI.

The serve path is section-aware chunking, local FastEmbed `bge-small`
plus BM25 in Qdrant, hybrid fusion with reciprocal rank fusion, and
a structured `/ask` that copies citation spans and refuses a quote
that is not verbatim. A LangGraph agent can plan extra searches. It
still has to answer through the same `ground()` check. Tools go
through a default-deny gate and a hash-chained audit log. MCP
exposes `search_filings` and `ask` on stdio. Azure Container Apps
Bicep is in the repo. Live apply is UNVERIFIED.

Measured, n=77 handwritten spans,
[`7388ac3eea43.json`](../evals/results/7388ac3eea43.json).
Section/hybrid Recall@5 is 0.169. Section BM25 at 0.182 versus
section dense at 0.078 is the gap that clears the 0.013 noise floor.
On n=15 comparison and multi-hop items, a fake planner reaches
0.400 Recall@5 against 0.267 for plain RAG. Gate red-team is 5/5.
Live OpenAI answer quality is UNVERIFIED.

The honest failure. Most gold spans never enter the hybrid top 5.
On the three section-BM25 diagnosed CET1 items, Citi is at rank 1
for `cc-h-001` and `cc-h-063`, and at rank 2 for `cc-h-013`. Next
work is a rerank arm, tighter chunk boundaries, and table-aware PDF
parsing, scored on the same 77 items.
